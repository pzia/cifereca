from __future__ import annotations
from cifereca_paris_formats import explicit_formats

import logging
import hashlib
import os
import httpx
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
import re
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from cifereca.plugin_api import (
    Account, HistoryItem, ItemDetail, Loan, SearchResult,
    AcquisitionOffer, AcquisitionOfferSet, AcquisitionPreferences,
    acquisition_format_projection, normalize_acquisition_format,
)
from cifereca_paris_client import AuthenticationError, ParisClient, ParisSummaryHTTPError, ParisLoanListError, ParisError
from cifereca_paris_trace import (
    public_book_id, public_content_type, public_http_status, public_site,
)
from cifereca.plugin_api import (
    AccountOptionField,
    BorrowerScopedOptionField,
    CredentialField,
    ProviderCapabilities,
    ProviderSearchCapabilities,
    ProviderCapabilityError,
    ProviderConfigurationError,
    ProviderDescriptor,
    ProviderPlugin,
    ProviderSession,
    ProviderError, Cancelled,
    LoanInventoryScope, LoanInventorySnapshot, LoanBranchCoverage,
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
    inspect_artifact_content,
    publish_artifact_content,
    sanitize_filename_component,
)

_TRACE_LOG = logging.getLogger("cifereca")


def _paris_core_admission_current(admission_current):
    """A revoked/raising Core lease cannot authorize a selected PNB GET."""
    try:
        return (admission_current is None or
                (callable(admission_current) and admission_current() is True))
    except Exception:
        return False


def _paris_mutating_pnb_endpoint(url: str) -> bool:
    """Reject borrower/quota endpoints even when the path is percent-encoded.

    Supplier gateways may decode paths more than once. A single unquote()
    would miss ASSA%2552edirect after its first decoding pass. Limit decoding
    work and refuse an excessively encoded path instead of guessing that the
    resulting GET is read-only. This has no network or URL-logging side effect.
    """
    try:
        path = urlparse(url).path
        if type(path) is not str:
            return True
        for _ in range(8):
            folded = path.casefold()
            if ("assaredirect" in folded
                    or "downloaddigitaldocument" in folded):
                return True
            decoded = unquote(path)
            if decoded == path:
                return False
            path = decoded
    except (TypeError, ValueError):
        return True
    return True  # Decoding depth unknown: fail closed.


def _pnb_direct_url_observed(raw: object, *, client: object) -> bool:
    """Only an actionable link at a supplier host; no remote GET or right test.

    Reuse the SAME host/TLS policy as the actual Paris content GET; merely
    parsing HTTPS is not enough to attest a provider-executable file route.
    """
    if (type(raw) is not str or not raw or raw != raw.strip()
            or any(c.isspace() or not c.isprintable() or c == chr(92) for c in raw)
            or "%0a" in raw.casefold() or "%0d" in raw.casefold()):
        return False
    try:
        parsed = urlparse(raw)
        if parsed.scheme and parsed.scheme != "https":
            return False
        if ((not parsed.scheme and (raw.startswith("//") or parsed.netloc))
                or not parsed.path or parsed.fragment or parsed.username
                or parsed.password):
            return False
        base_url = client.base_url
        validator = client._validate_pnb_redirect_url
        if type(base_url) is not str or not callable(validator):
            return False
        # A relative action is anchored to the currently configured Paris
        # account endpoint. The validator rejects foreign hosts, non-TLS,
        # arbitrary ports and embedded credentials without performing I/O.
        validator(urljoin(base_url.rstrip("/") + "/", raw))
        return True
    except Exception:
        # Supplier policy rejection is uncertainty, never a negative right.
        return False


def _paris_pnb_offer_id(scope, edition_id: str, right_id: str) -> str:
    """Stable opaque scoped action ID; never encode a signed PNB URL."""
    parts = (scope.identity_id, scope.provider_id, scope.credential_id,
             scope.account_id, edition_id, right_id, "dilicom:pnb-direct/1")
    return "paris.pnb:" + hashlib.sha256(chr(31).join(parts).encode("utf-8")).hexdigest()


def _paris_loan_branch_coverage(rows, upstream, medium, failed):
    """Qualify one successful scoped ListLoans read, independently of files."""
    def partial(reason):
        return LoanBranchCoverage(
            upstream=upstream.casefold(), medium=medium,
            completeness="partial", reason_code=reason,
        )

    if upstream in failed:
        return partial("branch_unavailable")
    seen = set()
    legacy = False
    for row in rows:
        if row.provider not in ("DILICOM", "SYRACUSE"):
            return partial("invalid_response")
        if row.provider != upstream:
            continue
        if row.medium != medium:
            return partial("invalid_response")
        if type(row.raw) is not dict or not row.raw:
            # A legacy/custom client may supply descriptive Loan objects,
            # but cannot attest provider identifiers or negative absence.
            legacy = True
            continue
        identifiers = (row.raw.get("Id") or row.raw.get("HoldingId"),
                       row.raw.get("RecordId"))
        if any(type(value) not in (str, int) or not 1 <= len(str(value)) <= 1024
               or any(c in str(value) for c in ("\x00", "\r", "\n"))
               for value in identifiers):
            return partial("invalid_response")
        loan_id, record_id = map(str, identifiers)
        if row.id != loan_id or row.record_id != record_id or loan_id in seen:
            return partial("invalid_response")
        seen.add(loan_id)
    return LoanBranchCoverage(
        upstream=upstream.casefold(), medium=medium,
        completeness="unknown" if legacy else "complete",
        pagination_complete=None if legacy else True,
    )



DESCRIPTOR = ProviderDescriptor(
    id="paris",
    display_name="Bibliothèques de Paris",
    description="Catalogue, compte lecteur et prêt numérique PNB des Bibliothèques de Paris.",
    # Versioned, read-only text contributions. State classification is the
    # generic CoreUI projection, never a connection/acquisition permission.
    search_status_notice_schema="cifereca.provider-search-ui/1",
    search_status_notices=(
        ("unknown", "Bibliothèques de Paris : source non connectée ; état du compte indéterminé."),
        ("plugin_missing", "Bibliothèques de Paris : plugin absent ; recherche en ligne indisponible."),
        ("plugin_disabled", "Bibliothèques de Paris : plugin désactivé ; recherche en ligne indisponible."),
        ("catalog_unavailable", "Bibliothèques de Paris : catalogue non disponible dans ce profil."),
        ("account_disconnected", "Bibliothèques de Paris : compte configuré mais source non connectée ; recherche en ligne indisponible."),
        ("account_disabled", "Bibliothèques de Paris : compte désactivé pour ce profil ; recherche en ligne indisponible."),
        ("account_unscoped", "Bibliothèques de Paris : source non connectée ; profil du compte à vérifier."),
        ("account_missing", "Bibliothèques de Paris : aucun compte rattaché à ce profil ; recherche en ligne indisponible."),
    ),
    state="ready",
    capabilities=ProviderCapabilities(
        authentication=True,
        multiple_credentials=True,
        linked_accounts=True,
        search=True,
        loans=True,
        history=True,
        bookings=False,
        holdings=True,
        borrow=True,
        digital_download=True,
        # Preserve the existing authorized Paris opening-sync path only.
        # Newly installed lenders remain default-deny until explicit opt-in.
        opening_loan_auto_acquisition=True,
        return_loan=False,
        renew_loan=True,
    ),
    search_capabilities=ProviderSearchCapabilities(
        fields=("all",),
        scopes=("all", "digital", "physical"),
        pagination="page",
        page_size=50,
        max_pages=101,
        notes=(
            "Recherche textuelle générale Syracuse ; aucun filtre auteur/ISBN/sujet natif attesté.",
            "all interroge les catalogues physique et numérique ; une panne de branche est signalée.",
            "Catalogue distinct des droits de prêt et des bibliothèques personnelles.",
        ),
    ),
    credential_fields=(
        CredentialField("username", "Identifiant", placeholder="Numéro de carte ou e-mail"),
        CredentialField("password", "Mot de passe", secret=True),
    ),
    homepage="https://bibliotheques.paris.fr",
    account_options=(
        AccountOptionField("base_url", "URL du portail",
                           default="https://bibliotheques.paris.fr",
                           legacy_config_key="base_url"),
        # #4065: these values apply to the PRIMARY Paris borrower ONLY.
        # Never inherit either value on a linked reader account: that requires
        # an independently identified borrower-specific setting and UI.
        AccountOptionField(
            "manual_loan_limit_primary_digital",
            "Compte principal · maximum personnel de prêts numériques",
            default="4", value_type="unsigned_integer", min_value=0, max_value=99,
            help_text="4 par défaut · DILICOM seulement. Plafond renseigné par vous, "
                      "sans effet sur les droits réels, l'emprunt ou la synchronisation. "
                      "Ne s'applique pas aux comptes lecteurs liés.",
        ),
        AccountOptionField(
            "manual_loan_limit_primary_physical",
            "Compte principal · maximum personnel de prêts physiques",
            default="4", value_type="unsigned_integer", min_value=0, max_value=99,
            help_text="4 par défaut · SYRACUSE seulement. Plafond renseigné par vous, "
                      "distinct du quota officiel et sans effet sur la prolongation. "
                      "Ne s'applique pas aux comptes lecteurs liés.",
        ),
    ),
    borrower_scoped_options=(BorrowerScopedOptionField(
        key="manual_loan_limits_linked",
        label="Plafond personnel du compte lié",
        branches=(("digital", "Numérique · DILICOM"),
                  ("physical", "Physique · SYRACUSE")),
        default="4", min_value=0, max_value=99,
        help_text="Indication locale uniquement. Les comptes liés sont séparés "
                  "du principal et les deux branches ne se cumulent pas ; "
                  "aucune incidence sur les droits de prêt ou la synchronisation.",
    ),),
)


def _paris_acquisition_offer_set(item: SearchResult) -> AcquisitionOfferSet:
    """Build acquisition choices only from structured Paris file-format evidence."""
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    values = metadata.get("available_formats")
    if not isinstance(values, (list, tuple)):
        values = ()
    formats: list[str] = []
    for value in values:
        normalized = normalize_acquisition_format(value)
        if normalized and normalized not in formats:
            formats.append(normalized)
    if not formats:
        return AcquisitionOfferSet()

    raw_evidence = metadata.get("format_evidence")
    evidence = tuple(
        str(value)
        for value in (raw_evidence if isinstance(raw_evidence, (list, tuple)) else ())
        if str(value).strip()
    )
    offer = AcquisitionOffer(
        offer_id=f"paris:{item.docbase}:{item.record_id}",
        media_type=item.content_type or "ebook",
        format=formats[0] if len(formats) == 1 else "",
        formats=tuple(formats),
        drm="unknown",
        source_kind="provider",
        source={
            "provider_id": "paris",
            "record_id": item.record_id,
            "docbase": item.docbase,
            "scenario": item.scenario,
        },
        format_evidence={
            "source": "paris_explicit",
            "fields": evidence,
        },
    )
    return AcquisitionOfferSet(
        offers=(offer,),
        preferences=AcquisitionPreferences(),
    )


def _project_paris_acquisition_formats(item: SearchResult) -> SearchResult:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    raw_evidence = metadata.get("format_evidence")
    choices = _paris_acquisition_offer_set(item)
    projection = acquisition_format_projection(choices)
    if raw_evidence:
        metadata["paris_format_evidence"] = raw_evidence
    metadata.update(projection)
    item.metadata = metadata
    return item


def _core_account_owners(refs, *, provider_id: str, account_id: str,
                         credential_id: str) -> tuple[str, ...] | None:
    """Match public Core AccountRef v2 by exact credential, not upstream ID.

    Two credentials may share a DILICOM borrower ID. A version-1 ref has no
    credential proof and stays visible as an ambiguous candidate; never
    silently drop it when v2 refs coexist for that upstream account.
    """
    if type(refs) not in (tuple, list):
        return None
    owners = []
    for ref in refs:
        if (getattr(ref, "provider_id", None) != provider_id
                or getattr(ref, "account_id", None) != account_id):
            continue
        version = getattr(ref, "scope_version", 1)
        if type(version) is not int or version not in (1, 2):
            return None
        if version == 2:
            credential = getattr(ref, "credential_id", None)
            if type(credential) is not str or not credential:
                return None
            if credential != credential_id:
                continue
        owners.append(
            ref.identity_id if type(getattr(ref, "identity_id", None)) is str else ""
        )
    return tuple(sorted(owners))


class ParisProviderSession(ProviderSession):
    def __init__(self, *, config: Any, diagnostics: Any,
                 identity_broker=None, **kwargs: Any) -> None:
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self.identity_broker = identity_broker
        from cifereca_paris_constants import BASE_URL
        base_url = str(self.options.get("base_url") or (config.paris_base_url(self.credential_id) if hasattr(config, "paris_base_url") else config.get("base_url", "")) or BASE_URL)
        timeout = float(self.options.get("request_timeout_seconds") or (config.request_timeout_seconds("paris", self.credential_id) if hasattr(config, "request_timeout_seconds") else config.get("request_timeout_seconds", 25)))
        self.diagnostics = diagnostics
        self.client = ParisClient(base_url=base_url, timeout=timeout, diagnostics=diagnostics)
        # Keep the v1 error facade for existing consumers; typed snapshots
        # capture immutable branch evidence from their own read instead.
        # The lock also prevents account reconnect/close during that read.
        self._loan_inventory_lock = RLock()
        self._loan_branch_failures: ContextVar[frozenset[str] | None] = ContextVar(
            "paris_loan_branch_failures", default=None,
        )
        # #4281: measurement-only provenance. No loan/rights cache or
        # additional provider request may be created from these counters.
        self._loan_read_consumer: ContextVar[str] = ContextVar(
            "paris_loan_read_consumer", default="loans",
        )
        self._loan_ebooks_attempts: ContextVar[int | None] = ContextVar(
            "paris_loan_ebooks_attempts", default=None,
        )
        # Only the synchronous Core opening-rights projection may install
        # an exact typed inventory in this task. No cross-call rights cache.
        self._same_cycle_rights_inventory: ContextVar[LoanInventorySnapshot | None] = ContextVar(
            "paris_same_cycle_rights_inventory", default=None,
        )
        # Tiny one-shot proof that Core's defensive copy stems from THIS
        # session's latest completed typed read, not a different borrower,
        # account instance or older session. Never retain loan rows here.
        # One-call, task-local exact inventory pin for selected delivery. The
        # candidate already fetched the current DILICOM card under the same
        # inventory lock; do not issue a redundant ListLoans before file GET.
        self._selected_delivery_inventory: ContextVar[LoanInventorySnapshot | None] = ContextVar(
            "paris_selected_delivery_inventory", default=None,
        )
        # One source-sync invocation, one source, one session generation.
        # No TTL/global rights cache and no reuse by manual actions.
        self._source_sync_offer_lease: ContextVar[dict | None] = ContextVar(
            "paris_source_sync_offer_lease", default=None,
        )
        self._last_typed_loan_marker = None
        self.account_status: dict[str, Any] = {"usable": None, "reason": None}
        self.pairing_status: dict[str, Any] = {"complete": None, "error": None}
        self._loan_catalog_metadata_cache: dict[str, dict[str, Any]] = {}
        # Core owns the persistent claim; never reuse one in this session.
        self._borrow_claim_attempts: set[str] = set()

    def _current_source_sync_offer_lease(self, account: Account) -> dict | None:
        """Fail closed on rebind, reconnect, linked account or task migration."""
        lease = self._source_sync_offer_lease.get()
        if type(lease) is not dict or type(account) is not Account:
            return None
        with self._loan_inventory_lock:
            try:
                scope = LoanInventoryScope.for_account(account)
                if (self.connected is not True
                        or lease["scope"] != scope
                        or lease["epoch"] != getattr(self, "_epoch", None)
                        or lease["accounts_id"] != id(self._accounts)):
                    return None
                owners = [
                    a for a in self._accounts if type(a) is Account
                    and LoanInventoryScope.for_account(a) == scope
                ]
                if len(owners) != 1 or id(owners[0]) != lease["account_object_id"]:
                    return None
                return lease
            except (TypeError, AttributeError, ValueError, KeyError):
                return None

    @contextmanager
    def source_sync_inventory(self, account: Account):
        """Ephemeral exact evidence only inside one Core source Sync.

        Core already serializes acquisition with the shared writer lock.
        The lease cannot outlive its ContextVar token or be used by a manual
        selected offer after that source Sync. No HTTP or rights write here.
        """
        if type(account) is not Account:
            raise ProviderCapabilityError("paris_source_sync_account_invalid")
        with self._loan_inventory_lock:
            scope = LoanInventoryScope.for_account(account)
            owners = [
                a for a in self._accounts if type(a) is Account
                and LoanInventoryScope.for_account(a) == scope
            ]
            if (self.connected is not True
                    or scope.provider_id != self.provider_id
                    or scope.credential_id != self.credential_id
                    or len(owners) != 1
                    or self._source_sync_offer_lease.get() is not None):
                raise ProviderCapabilityError("paris_source_sync_lease_unavailable")
            lease = {
                "scope": scope, "epoch": getattr(self, "_epoch", None),
                "accounts_id": id(self._accounts),
                "account_object_id": id(owners[0]),
                "inventory": None, "rights": None,
            }
            token = self._source_sync_offer_lease.set(lease)
        try:
            yield
        finally:
            # Source-scoped evidence is erased regardless of success, timeout,
            # cancellation or exception. Never retain an inter-cycle URL.
            lease["inventory"] = None
            lease["rights"] = None
            self._source_sync_offer_lease.reset(token)

    @staticmethod
    def _account_unusable_reason(summary: dict[str, Any]) -> str | None:
        """Return a stable reason only for explicit unusable-account signals."""
        for key in ("IsDisabled", "Disabled", "IsBlocked", "Blocked", "IsSuspended", "Suspended"):
            if summary.get(key) is True:
                return key
        for key in ("IsActive", "Active", "Enabled"):
            if key in summary and summary.get(key) is False:
                return key
        for key in ("Status", "State", "AccountStatus", "MembershipStatus"):
            raw = summary.get(key)
            if raw is None:
                continue
            value = unicodedata.normalize("NFKD", str(raw)).encode("ascii", "ignore").decode().casefold().strip()
            if value in {"disabled", "inactive", "blocked", "suspended", "expired", "desactive", "bloque", "suspendu", "expire"}:
                return f"{key}:{raw}"
        return None

    def _decorate_account(self, account: Account) -> Account:
        account.provider_id = self.provider_id
        account.credential_id = self.credential_id
        account.provider_label = self.descriptor.display_name
        return account

    def connect(self) -> list[Account]:
        # A reconnect replaces the authenticated client and account list.
        # Serialize it with the complete two-branch inventory snapshot so a
        # single published observation cannot span two sessions.
        with self._loan_inventory_lock:
            return self._connect_locked()

    def _connect_locked(self) -> list[Account]:
        # A new authentication attempt replaces the previous session's
        # authority even if login, summary or credential validation fails.
        # Never leave stale accounts usable after a refused reconnect.
        # Successful connection publishes its fresh accounts at the end.
        self.connected = False
        self._accounts = []
        username = str(self.credentials.get("username") or "").strip()
        password = str(self.credentials.get("password") or "")
        if not username or not password:
            raise ProviderConfigurationError("Bibliothèques de Paris : identifiant et mot de passe requis")
        try:
            self.client.login(username, password)
        except Exception as exc:
            # A 403 upstream must not be misdiagnosed as bad credentials.
            # Only our typed AuthenticationError carries approved reason
            # codes; untrusted exception text and cookies are never logged.
            reason = exc.code if isinstance(exc, AuthenticationError) else "paris_login_failed"
            try:
                self.diagnostics.add(
                    "AUTH", "Paris connection", "error",
                    detail={"provider": "paris", "code": reason},
                )
            except Exception:
                pass
            raise
        summary: dict[str, Any] | None = None
        first_summary_error: Exception | None = None
        for upstream in ("SYRACUSE", "DILICOM"):
            try:
                summary = self.client.summary(upstream, "")
                break
            except Exception as exc:
                if first_summary_error is None:
                    first_summary_error = exc
                try:
                    detail = {
                        "provider": self.provider_id,
                        "credential_id": self.credential_id,
                        "upstream": upstream,
                        "error": type(exc).__name__,
                    }
                    if isinstance(exc, ParisSummaryHTTPError):
                        detail["code"] = exc.code
                    self.diagnostics.add(
                        "API", f"summary {upstream}", "error", detail=detail,
                    )
                except Exception:
                    pass
        if summary is None:
            assert first_summary_error is not None
            try:
                self.diagnostics.add(
                    "AUTH", "Paris connection", "error",
                    detail={"provider": "paris", "code": "paris_summary_unavailable"},
                )
            except Exception:
                pass
            raise first_summary_error
        unusable_reason = self._account_unusable_reason(summary)
        if unusable_reason:
            self.account_status = {"usable": False, "reason": unusable_reason}
            try:
                self.diagnostics.add(
                    "AUTH", "account status", "error",
                    detail={
                        "provider": self.provider_id,
                        "credential_id": self.credential_id,
                        "usable": False,
                        "reason": unusable_reason,
                    },
                )
            except Exception:
                pass
            raise ProviderConfigurationError(
                "Bibliothèques de Paris : compte désactivé ou inutilisable ; "
                "synchronisation et acquisition arrêtées avant ADEPT"
            )
        self.account_status = {"usable": True, "reason": None}
        primary = self._decorate_account(Account(
            display_name=str(summary.get("DisplayName") or self.label or "Compte principal"),
            barcode=str(summary.get("Barcode") or ""),
            unique_id="",
            is_primary=True,
        ))
        try:
            linked = [self._decorate_account(x) for x in self.client.pairings()]
            pairing_errors = list(getattr(self.client, "last_pairing_errors", ()) or ())
            error_types = sorted({
                str(row.get("type") or "Error")
                for row in pairing_errors
                if isinstance(row, dict)
            })
            self.pairing_status = {
                "complete": not bool(pairing_errors),
                "error": ",".join(error_types) if error_types else None,
            }
        except Exception as exc:
            linked = []
            self.pairing_status = {
                "complete": False,
                "error": type(exc).__name__,
            }
            try:
                self.diagnostics.add(
                    "API", "pairings", "error",
                    detail={
                        "provider": self.provider_id,
                        "credential_id": self.credential_id,
                        "error": type(exc).__name__,
                    },
                )
            except Exception:
                pass
        self._accounts = [primary, *linked]
        self.connected = True
        return self.accounts

    def connect_with_credentials(self, username: str, password: str) -> list[Account]:
        with self._loan_inventory_lock:
            self.credentials = {"username": username, "password": password}
            return self.connect()

    def close(self) -> None:
        with self._loan_inventory_lock:
            self.client.close()
            self.connected = False

    @staticmethod
    def _decorate(items: list[Any], provider_id: str, credential_id: str) -> list[Any]:
        for item in items:
            if hasattr(item, "source_provider"):
                item.source_provider = provider_id
            if hasattr(item, "source_credential"):
                item.source_credential = credential_id
            # Catalogue identifiers are only unique inside a provider. Prefix
            # the UI identity while preserving `record_id` for upstream calls.
            if isinstance(item, SearchResult):
                item.id = f"{provider_id}:{item.id}"
        return items

    @staticmethod
    def _fold_catalog_text(value: Any) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()

    @staticmethod
    def _loan_embedded_metadata(loan: Loan) -> dict[str, Any]:
        raw = loan.raw if isinstance(loan.raw, dict) else {}
        additional = raw.get("AdditionalProperties") if isinstance(raw.get("AdditionalProperties"), dict) else {}
        author = (
            additional.get("Auteur") or additional.get("Author") or additional.get("Creator")
            or raw.get("Auteur") or raw.get("Author") or raw.get("Creator")
        )
        formats, _evidence = explicit_formats(('AdditionalProperties', additional), ('Loan', raw))
        media_format = formats[0] if len(formats) == 1 else None
        return {
            "author": str(author).strip() if author else None,
            "format": str(media_format).strip() if media_format else None,
        }

    def _enrich_digital_loan_metadata(self, loan: Loan) -> None:
        """Attach bounded catalogue metadata without changing the public Loan contract.

        DILICOM ListLoans does not reliably expose author/media format in the
        normalized Loan model.  Keep the authoritative upstream loan untouched,
        but add a plugin-owned metadata projection under ``raw['_cifereca']``.
        One bounded EBOOKS lookup is attempted only when the loan payload itself
        is missing author or format.  Failure to enrich must never hide a loan.
        """
        if loan.medium != "digital" or str(loan.provider).upper() != "DILICOM":
            return

        embedded = self._loan_embedded_metadata(loan)
        record_id = str(loan.record_id or "").strip()
        cache_key = record_id or f"title:{self._fold_catalog_text(loan.title)}"
        metadata = dict(self._loan_catalog_metadata_cache.get(cache_key) or {})

        if not metadata and embedded.get("author") and embedded.get("format"):
            metadata = embedded
        elif not metadata:
            rows: list[SearchResult] = []
            queries = [record_id] if record_id else []
            title = str(loan.title or "").strip()
            if title and self._fold_catalog_text(title) not in {self._fold_catalog_text(q) for q in queries}:
                queries.append(title)
            try:
                matched: SearchResult | None = None
                for query in queries[:2]:
                    attempts = self._loan_ebooks_attempts.get()
                    if attempts is not None:
                        self._loan_ebooks_attempts.set(attempts + 1)
                    rows = self.client.search_catalog(query, scenario="EBOOKS", result_size=12)
                    if record_id:
                        matched = next((row for row in rows if str(row.record_id) == record_id), None)
                    if matched is None and title:
                        folded_title = self._fold_catalog_text(title)
                        title_matches = [row for row in rows if self._fold_catalog_text(row.title) == folded_title]
                        if len(title_matches) == 1:
                            matched = title_matches[0]
                    if matched is not None:
                        break
                if matched is not None:
                    metadata = {
                        "author": matched.author or embedded.get("author"),
                        "format": matched.format or embedded.get("format"),
                        "type": matched.type,
                        "publisher": matched.publisher,
                        "published_at": matched.published_at,
                        "thumbnail_url": matched.thumbnail_url,
                        "record_id": matched.record_id,
                        "source": "catalog:EBOOKS",
                    }
                else:
                    metadata = {**embedded, "source": "loan-payload"}
            except Exception as exc:
                metadata = {**embedded, "source": "loan-payload", "catalog_enrichment_error": type(exc).__name__}
                try:
                    self.diagnostics.add(
                        "CATALOG", "loan metadata enrichment", "error",
                        detail={
                            "provider": self.provider_id,
                            "record_id_present": bool(record_id),
                            "error": type(exc).__name__,
                        },
                        level="detailed",
                    )
                except Exception:
                    pass

        if cache_key:
            self._loan_catalog_metadata_cache[cache_key] = dict(metadata)
        if not isinstance(loan.raw, dict):
            loan.raw = {}
        loan.raw["_cifereca"] = {k: v for k, v in metadata.items() if v not in {None, ""}}
        # Plugin API 0.9.3 promotes the two library-critical facets to the
        # normalized Loan contract. Keep raw metadata for compatibility/detail,
        # but make Library filtering independent from that implementation detail.
        if not loan.author and metadata.get("author"):
            loan.author = str(metadata["author"])
        if not loan.media_format and metadata.get("format"):
            loan.media_format = str(metadata["format"]).strip().lower() or None
        if not loan.thumbnail_url and metadata.get("thumbnail_url"):
            loan.thumbnail_url = str(metadata["thumbnail_url"])

    def loans(self, account: Account) -> list[Loan]:
        with self._loan_inventory_lock:
            return self._read_loans_for_account(account)

    def _read_loans_for_account(self, account: Account) -> list[Loan]:
        """Read the two scoped branches with private, per-read evidence.

        Both loans() and the typed snapshot reuse the same GETs; only the
        typed snapshot may attest branch coverage. No mutable v1 error facade.
        """
        # A new private inventory request supersedes any previously offered
        # same-cycle observation, including before a provider failure.
        self._last_typed_loan_marker = None
        # DILICOM (digital/PNB) and SYRACUSE (physical) are independent upstream
        # services. One transient failure must not hide all loans from the other.
        rows: list[Loan] = []
        errors: list[dict[str, str]] = []
        # The two independent private branches MUST address the *same*
        # borrower. Core can mutate an Account while DILICOM is in flight;
        # rereading unique_id for SYRACUSE would make a foreign-account GET
        # before the typed reader has a chance to reject stale results.
        borrower_scope = (
            account.unique_id, account.identity_id,
            account.provider_id, account.credential_id,
        ) if type(account) is Account else None
        if borrower_scope is None or borrower_scope[2:] != (
                self.provider_id, self.credential_id):
            raise ProviderConfigurationError("account_scope_mismatch")
        borrower_id = borrower_scope[0]
        # The typed snapshot already selected one exact live Account, but
        # legacy loans(account) may be called directly while Core migrates.
        # Never send a private DILICOM/SYRACUSE GET for a disconnected,
        # removed, foreign, or ambiguous borrower.
        def matching_accounts():
            return [
                active for active in self._accounts
                if type(active) is Account
                and (active.unique_id, active.identity_id,
                     active.provider_id, active.credential_id) == borrower_scope
            ]

        initial = matching_accounts()
        bound = initial[0] if len(initial) == 1 else None
        broker = self.identity_broker

        def broker_owner_binding():
            """Pin the actual Core owner, not only a mutable Account object.

            Legacy inventory remains readable with an absent or ambiguous
            broker, but a mid-read change must never permit a second private
            GET under a different or newly selected Core owner.
            """
            if broker is None:
                return None
            try:
                # The Core broker is scoped by target identity. Enumerating
                # every identity incorrectly makes a distinct borrower of the
                # same upstream account look like an ambiguous owner.
                refs = broker.accounts(borrower_scope[1])
                if type(refs) not in (list, tuple):
                    return None
                key = borrower_scope[0] if borrower_scope[0] else (
                    f"{self.provider_id}:{self.credential_id}:primary"
                )
                return _core_account_owners(
                    refs, provider_id=self.provider_id,
                    account_id=key, credential_id=self.credential_id,
                )
            except Exception:
                return None

        initial_owner = broker_owner_binding()

        def scope_current():
            return (
                self.connected is True
                and self.identity_broker is broker
                and (broker is None or
                     broker_owner_binding() == initial_owner)
                and (account.unique_id, account.identity_id,
                     account.provider_id, account.credential_id) == borrower_scope
                and bound is not None
                and (bound.unique_id, bound.identity_id,
                     bound.provider_id, bound.credential_id) == borrower_scope
                and len(current := matching_accounts()) == 1
                and current[0] is bound
            )

        for upstream in ("DILICOM", "SYRACUSE"):
            if not scope_current():
                raise ProviderConfigurationError("account_scope_mismatch")
            started = time.perf_counter()
            stage_status = "success"
            stage_rows = 0
            try:
                branch_rows = self.client.loans(upstream, borrower_id)
                stage_rows = len(branch_rows)
                rows.extend(branch_rows)
            except Cancelled:
                stage_status = "cancelled"
                # Cooperative cancellation is NOT an unavailable branch.
                # Stop the second upstream GET and never publish a partial
                # inventory or synthetic negative-rights evidence.
                raise
            except Exception as exc:
                stage_status = "failed"
                # Neither branch failure nor sibling success proves an empty
                # inventory. Keep the compatibility code and attach only the
                # separately typed HTTP/transport reason when attested.
                reason = (
                    exc.code if isinstance(exc, ParisLoanListError) else
                    "paris_loans_transport_timeout" if isinstance(exc, httpx.TimeoutException) else
                    "paris_loans_transport_failed" if isinstance(exc, httpx.RequestError) else
                    "paris_loans_inventory_shape_invalid"
                    if type(exc) is ParisError and str(exc) == "paris_loans_inventory_shape_invalid" else
                    None
                )
                failure = {
                    "upstream": upstream,
                    "code": "paris_loans_upstream_failed",
                    **({"reason_code": reason} if reason is not None else {}),
                }
                errors.append(failure)
                try:
                    self.diagnostics.add(
                        "API", f"loans {upstream}", "error",
                        detail={"provider": "paris", **failure},
                    )
                except Exception:
                    pass
            finally:
                # One measurement per actual upstream attempt. A failed or
                # cancelled branch is not a complete, empty loan inventory.
                _TRACE_LOG.info(
                    "TRACE paris list-loans timing phase=branch consumer=%s"
                    " upstream=%s identity_id=%s credential_id=%s borrower_id=%s"
                    " status=%s duration_ms=%s loans=%s",
                    self._loan_read_consumer.get(), upstream, borrower_scope[1],
                    borrower_scope[3], borrower_id, stage_status,
                    max(0, int((time.perf_counter() - started) * 1000)), stage_rows,
                )
            # This check must run OUTSIDE the exception handler: a borrower
            # change is an authorization failure, never merely a partial
            # provider branch failure that permits another private GET.
            if not scope_current():
                raise ProviderConfigurationError("account_scope_mismatch")
        # Closed, per-read branch evidence; never publish a mutable error
        # side channel or use a v1 empty list as negative-rights evidence.
        self._loan_branch_failures.set(frozenset(error["upstream"] for error in errors))
        if errors and not rows and len(errors) == 2:
            raise ProviderConfigurationError(
                "Bibliothèques de Paris : inventaires DILICOM et SYRACUSE indisponibles"
            ) from None
        rows = self._decorate(rows, self.provider_id, self.credential_id)
        enriched_at = time.perf_counter()
        attempts_token = self._loan_ebooks_attempts.set(0)
        try:
            # #4281: an exact rights.observation snapshot uses the already
            # scoped ListLoans rows and upstream IDs only. Author, thumbnail
            # and EBOOKS catalogue lookups cannot attest loan rights, so
            # omit that optional work on the rights-only critical path.
            # Inventory/ordinary loan readers retain their descriptive data.
            if self._loan_read_consumer.get() != "rights_observation":
                for loan in rows:
                    self._enrich_digital_loan_metadata(loan)
        finally:
            # Catalogue lookups are optional descriptive calls, never an
            # additional ListLoans read or a proof of current entitlements.
            attempts = self._loan_ebooks_attempts.get()
            self._loan_ebooks_attempts.reset(attempts_token)
            _TRACE_LOG.info(
                "TRACE paris list-loans timing phase=metadata consumer=%s"
                " identity_id=%s credential_id=%s borrower_id=%s"
                " loans=%s ebooks_attempted=%s duration_ms=%s",
                self._loan_read_consumer.get(), borrower_scope[1],
                borrower_scope[3], borrower_id, len(rows), attempts,
                max(0, int((time.perf_counter() - enriched_at) * 1000)),
            )
        # Persist the exact borrower on each loan. Paris uses the empty string
        # for the primary account, which must not be confused with "unknown".
        if not scope_current():
            raise ProviderConfigurationError("account_scope_mismatch")
        for loan in rows:
            loan.source_account_id = borrower_id or "primary"
        return sorted(rows, key=lambda x: (x.due_at is None, x.due_at or "", x.title.casefold()))

    def loan_inventory_snapshot(self, account: Account) -> LoanInventorySnapshot:
        with self._loan_inventory_lock:
            token = self._loan_read_consumer.set("loan_inventory")
            try:
                snapshot = self._snapshot_for_account(account)
                # The Core adapter makes defensive copies. Keep only a
                # short marker and the live Account object's identity, not
                # a second LoanInventorySnapshot or a persistent rights cache.
                matches = [
                    live for live in self._accounts if type(live) is Account
                    and LoanInventoryScope.for_account(live) == snapshot.scope
                ]
                if (snapshot.status == "success"
                        and snapshot.observed_at
                        and snapshot.branch_claims_complete("dilicom", "digital")
                        and snapshot.branch_claims_complete("syracuse", "physical")
                        and len(matches) == 1):
                    self._last_typed_loan_marker = (
                        snapshot.observed_at, snapshot.scope, id(matches[0]),
                        id(self._accounts), getattr(self, "_epoch", None),
                    )
                return snapshot
            finally:
                self._loan_read_consumer.reset(token)

    def _snapshot_for_account(self, account: Account) -> LoanInventorySnapshot:
        """One scoped read; each qualified ListLoans branch is complete.

        The official endpoint returns the current whole list, including zero
        loans. HTTP/shape/pagination failures are rejected by the client;
        malformed or ambiguous row IDs cannot certify absent loans here.
        No local file or ADEPT success is inferred from this certificate.
        """
        # A borrower may be passed as an Account copy by the Core; choose
        # the unique live account by its scoped values, but then pin that
        # exact instance across the two-branch read. Attribute-only checks
        # could miss A→A replacement under a concurrent broker/session edit.
        scope = (
            account.unique_id, account.identity_id,
            account.provider_id, account.credential_id,
        ) if type(account) is Account else None

        def matching_accounts():
            return [
                active for active in self._accounts
                if type(active) is Account
                and (active.unique_id, active.identity_id,
                     active.provider_id, active.credential_id) == scope
            ] if scope is not None else []

        initial = matching_accounts()
        bound = initial[0] if len(initial) == 1 else None

        def still_bound() -> bool:
            return (
                self.connected is True
                and scope is not None
                and scope[2:] == (self.provider_id, self.credential_id)
                and (account.unique_id, account.identity_id,
                     account.provider_id, account.credential_id) == scope
                and bound is not None
                and (bound.unique_id, bound.identity_id,
                     bound.provider_id, bound.credential_id) == scope
                and len(current := matching_accounts()) == 1
                and current[0] is bound
            )

        if not still_bound():
            raise ProviderConfigurationError("account_scope_mismatch")
        # The typed reader does not call loans(). Its scoped ContextVar
        # cannot be replaced by a different borrower's read.
        token = self._loan_branch_failures.set(None)
        try:
            rows = self._read_loans_for_account(account)
            failed = self._loan_branch_failures.get()
        finally:
            self._loan_branch_failures.reset(token)
        # One upstream response can race with an explicit disconnect,
        # a changed credential or linked-reader removal. Never publish that
        # batch under the former account's typed inventory envelope.
        if not still_bound():
            raise ProviderConfigurationError("account_scope_mismatch")
        if failed is None:
            # A substituted reader that supplies rows without branch evidence
            # cannot claim typed success, even if the v1 facade appears empty.
            raise ProviderConfigurationError("loan_inventory_evidence_missing")
        # Historical Paris Loan producers do not always carry the local
        # Core identity. The authenticated account argument is the only
        # authorized scope for this read. Never overwrite a contradictory
        # identity supplied by an upstream row.
        normalized = tuple(
            replace(row, identity_id=account.identity_id)
            if row.identity_id is None else row
            for row in rows
        )
        branches = tuple(
            _paris_loan_branch_coverage(normalized, upstream, medium, failed)
            for upstream, medium in (
                ("DILICOM", "digital"), ("SYRACUSE", "physical")
            )
        )
        return LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(account),
            loans=normalized,
            status="partial" if any(b.completeness == "partial" for b in branches) else "success",
            branches=branches,
            observed_at=datetime.now(timezone.utc).isoformat(),
            reason_code=("branch_unavailable" if failed else
                         "invalid_response" if any(b.reason_code == "invalid_response"
                                                   for b in branches) else None),
        )

    def rights_observation_snapshot(self, account: Account) -> ProviderRightsSnapshot:
        """Read-only DILICOM loan claims from the already-scoped two-branch GET.

        Paris loan normalization has a *descriptive* fallback based on title or
        a generated UUID when upstream Id/HoldingId is missing. Those fallbacks
        are NOT admissible provider right identifiers; only actual DILICOM
        response IDs and RecordId may be promoted to this versioned envelope.
        """
        with self._loan_inventory_lock:
            # A Core account copy is permitted, but only one *live* account
            # instance with these exact values may authorize the observation.
            # Pin its instance beyond the legacy inventory reader's last
            # check: a caller can replace _accounts during rights projection.
            scope_key = (
                account.unique_id, account.identity_id,
                account.provider_id, account.credential_id,
            ) if type(account) is Account else None

            def bound_accounts():
                return [
                    current for current in self._accounts
                    if type(current) is Account
                    and (current.unique_id, current.identity_id,
                         current.provider_id, current.credential_id) == scope_key
                ] if scope_key is not None else []

            def core_owner_binding():
                broker = self.identity_broker
                if broker is None or scope_key is None:
                    return None
                try:
                    # Resolve ownership in the exact target identity; never
                    # treat an unrelated library profile as a competing owner.
                    refs = broker.accounts(scope_key[1])
                    if type(refs) not in (tuple, list):
                        return None
                    # Core's public AccountRef gives the primary borrower
                    # with empty upstream ID a credential-qualified synthetic
                    # account_id. Preserve the original empty borrower in the
                    # rights scope; only the lookup key is synthetic.
                    core_account_id = (
                        scope_key[0] if scope_key[0] else
                        f"{self.provider_id}:{self.credential_id}:primary"
                    )
                    owners = _core_account_owners(
                        refs, provider_id=self.provider_id,
                        account_id=core_account_id, credential_id=self.credential_id,
                    )
                    return (id(broker), owners) if owners is not None else None
                except Exception:
                    return None

            # Only Core can attest that a mutable borrower belongs to this
            # local identity, including the empty primary Paris account ID.
            owner_binding = core_owner_binding()
            if (scope_key is None or scope_key[2:] !=
                    (self.provider_id, self.credential_id)
                    or type(scope_key[1]) is not str or not scope_key[1]
                    or owner_binding is None
                    or owner_binding[1] != (scope_key[1],)):
                raise ProviderCapabilityError("paris_rights_owner_unverified")
            original = bound_accounts()
            bound = original[0] if len(original) == 1 else None
            if bound is None:
                raise ProviderConfigurationError("account_scope_mismatch")
            same_cycle = self._same_cycle_rights_inventory.get()
            if same_cycle is not None:
                # The prior read was authenticated and normalized through the
                # Core typed inventory adapter in the *same* source Sync phase.
                # Consume only the exact complete two-branch immutable
                # envelope. Never fetch a sibling account or silently use
                # a partial/foreign batch to infer absent rights.
                self._same_cycle_rights_inventory.set(None)
                if (type(same_cycle) is not LoanInventorySnapshot
                        or same_cycle.scope != LoanInventoryScope.for_account(account)
                        or same_cycle.status != "success"
                        or not same_cycle.branch_claims_complete("dilicom", "digital")
                        or not same_cycle.branch_claims_complete("syracuse", "physical")):
                    raise ProviderCapabilityError("paris_same_cycle_inventory_unverified")
                inventory = same_cycle
            else:
                token = self._loan_read_consumer.set("rights_observation")
                try:
                    inventory = self._snapshot_for_account(account)
                finally:
                    self._loan_read_consumer.reset(token)
            scope = inventory.scope
            # A succeeded SYRACUSE response cannot turn a failed DILICOM
            # read into an empty but apparently successful rights branch.
            # Use typed branch coverage, never the mutable v1 facade.
            dilicom_failed = any(
                branch.upstream == "dilicom"
                and branch.reason_code == "branch_unavailable"
                for branch in inventory.branches
            )
            observed = datetime.now(timezone.utc).isoformat()
            rights_by_id: dict[str, ProviderRightObservation] = {}
            ambiguous_right_ids: set[str] = set()
            for loan in (() if dilicom_failed else inventory.loans):
                if loan.provider != "DILICOM" or loan.medium != "digital":
                    continue
                raw = loan.raw
                if type(raw) is not dict:
                    continue
                right_id = raw.get("Id") or raw.get("HoldingId")
                edition_id = raw.get("RecordId")
                # bool is an int in Python, but not a stable upstream ID.
                if (type(right_id) not in (str, int)
                        or type(edition_id) not in (str, int)):
                    continue
                right_text, edition_text = str(right_id), str(edition_id)
                # LoanInventory v1 permits longer IDs and line breaks in
                # legacy rows, but rights.observation/1 does not. One invalid
                # upstream card must not abort all other positive loan rights.
                if (not 1 <= len(right_text) <= 256
                        or not 1 <= len(edition_text) <= 256
                        or any(c in right_text or c in edition_text
                               for c in ("\x00", "\r", "\n"))
                        or loan.id != right_text
                        or loan.record_id != edition_text):
                    continue
                # The same provider loan ID on two upstream cards is an
                # ambiguous right, even when both cards cite one edition.
                # Exclude *both* occurrences without invalidating other
                # attested DILICOM rights in this partial inventory.
                right_key = right_text
                if right_key in ambiguous_right_ids:
                    continue
                if right_key in rights_by_id:
                    rights_by_id.pop(right_key)
                    ambiguous_right_ids.add(right_key)
                    continue
                # This private ListLoans card can attest a *file action*
                # without attesting a valid/active entitlement. No PNB GET
                # happens while observing the inventory. Actual download
                # requires a fresh exact-card recheck and the Core consent.
                has_pnb = _pnb_direct_url_observed(loan.ebook_url, client=self.client)
                rights_by_id[right_key] = ProviderRightObservation(
                    edition_id=edition_text, right_id=right_key,
                    kind="loan", state="unknown",
                    evidence_kind="provider_response",
                    evidence_ref="paris.dilicom.listloans",
                    observed_at=observed,
                    delivery="available" if has_pnb else "unknown",
                    delivery_offer_id=(
                        _paris_pnb_offer_id(scope, edition_text, right_key)
                        if has_pnb else ""
                    ),
                )
            # Core may mutate the mutable borrower independently from the
            # session lock. Revalidate its exact scope before publication.
            current = bound_accounts()
            if (not self.connected
                    or (self.provider_id, self.credential_id)
                    != (scope.provider_id, scope.credential_id)
                    or LoanInventoryScope.for_account(account) != scope
                    or len(current) != 1 or current[0] is not bound
                    or (account.unique_id, account.identity_id,
                        account.provider_id, account.credential_id) != scope_key
                    or core_owner_binding() != owner_binding):
                raise ProviderConfigurationError("account_scope_mismatch")
            # ListLoans is one authenticated, non-paginated DILICOM inventory
            # response for this exact borrower. Successful consultation is
            # complete by default for *observations*, including zero loans.
            # A missing upstream branch, invalid/misclassified card or
            # duplicate right ID must keep this evidence partial. Physical
            # SYRACUSE errors are independent and cannot erase a successful
            # DILICOM read. This claim never authorizes negative rights or
            # a return: LoanInventorySnapshot's stricter completeness stays
            # unchanged until its separate legacy return semantics are retired.
            dilicom_branches = [
                branch for branch in inventory.branches
                if branch.upstream == "dilicom" and branch.medium == "digital"
            ]
            digital_candidates = [
                loan for loan in inventory.loans
                if loan.provider == "DILICOM" or loan.medium == "digital"
            ]
            complete = (
                type(inventory) is LoanInventorySnapshot
                and inventory.status in ("success", "partial")
                and len(dilicom_branches) == 1
                and dilicom_branches[0].reason_code is None
                and not dilicom_failed
                and not ambiguous_right_ids
                and len(rights_by_id) == len(digital_candidates)
                and all(
                    loan.provider == "DILICOM" and loan.medium == "digital"
                    for loan in digital_candidates
                )
            )
            report = ProviderRightsSnapshot(
                scope=ProviderRightsScope(
                    scope.identity_id, scope.provider_id,
                    scope.credential_id, scope.account_id,
                ),
                rights=tuple(rights_by_id.values()),
                status=("failed" if dilicom_failed else
                        "success" if complete else "partial"),
                coverage=("unknown" if dilicom_failed else
                          "complete" if complete else "partial"),
                pagination_complete=bool(complete), branch="dilicom",
                observed_at=observed, snapshot_ref="paris.dilicom.listloans",
            )
            # Only an exact, complete two-branch typed inventory can serve
            # as an offer proof inside this already-locked Core source cycle.
            # A failed/partial read never replaces an earlier attestation.
            lease = self._current_source_sync_offer_lease(account)
            if (lease is not None and complete
                    and inventory.status == "success"
                    and inventory.branch_claims_complete("syracuse", "physical")
                    and report.status == "success"):
                lease["inventory"] = inventory
                lease["rights"] = report
            return report

    def history(self, account: Account) -> list[HistoryItem]:
        return self._decorate(self.client.all_history(account.unique_id), self.provider_id, self.credential_id)

    @staticmethod
    def _search_arguments(query: str, scope: str, page: int, field: str) -> tuple[str, str]:
        # Syracuse search is a general text query. Do not advertise an
        # author/ISBN/subject restriction that the upstream client ignores.
        if type(field) is not str or field.strip().casefold() != "all":
            raise ProviderCapabilityError("Bibliothèques de Paris : unsupported_field")
        if type(scope) is not str or scope.strip().casefold() not in ("all", "digital", "physical"):
            raise ProviderCapabilityError("Bibliothèques de Paris : unsupported_scope")
        if type(page) is not int or not 0 <= page <= 100:
            # A malformed index is not a valid empty page and must never
            # reach the Syracuse SearchService or its retry/fallback route.
            raise ProviderCapabilityError("Bibliothèques de Paris : invalid_page")
        # Syracuse accepts general text queries only. Never stringify a
        # cached object or forward bidi/control characters into HTTP, where
        # a malformed input could otherwise masquerade as a catalogue zero.
        if query is None:
            query = ""
        if (type(query) is not str or len(query) > 2048
                or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"}
                       for char in query)):
            raise ProviderCapabilityError("Bibliothèques de Paris : invalid_query")
        # The public capability validator accepts case/whitespace variants,
        # but ParisClient dispatches exact tokens. Pass the validated scope
        # rather than implicitly widening " DIGITAL " to all catalogues.
        return query, scope.strip().casefold()

    def search(self, query: str, scope: str = "all", page: int = 0, field: str = "all") -> list[SearchResult]:
        # An unsupported field/scope is a new failed attempt.
        self.last_search_warnings = []
        self.last_search_response_facets = []
        query, scope = self._search_arguments(query, scope, page, field)
        try:
            rows = self._decorate(
                self.client.search(query, scope, page),
                self.provider_id,
                self.credential_id,
            )
            return [_project_paris_acquisition_formats(item) for item in rows]
        finally:
            self.last_search_warnings = list(self.client.last_search_warnings)
            self.last_search_response_facets = [
                dict(facet) for facet in getattr(self.client, "last_search_response_facets", ())
            ]

    def search_with_response_facets(
        self, query: str, scope: str = "all", *, field: str = "all",
        content_types: tuple[str, ...] = (),
    ):
        # Import the optional contract only when a compatible Core invokes it.
        from cifereca.plugin_api import (
            CatalogSearchFacet, CatalogSearchFacetValue, ProviderSearchResponse,
        )

        self.last_search_warnings = []
        self.last_search_response_facets = []
        query, scope = self._search_arguments(query, scope, 0, field)
        try:
            rows, facets, warnings = self.client.search_response(query, scope)
        except Exception:
            self.last_search_warnings = list(self.client.last_search_warnings)
            raise
        self.last_search_warnings = warnings
        self.last_search_response_facets = [dict(facet) for facet in facets]
        projected = tuple(
            CatalogSearchFacet(
                facet["scenario"], facet["page"], facet["field"], facet["label"],
                tuple(CatalogSearchFacetValue(**value) for value in facet["values"])
                if facet["values_schema"] == "label_count_selected_v1" else None,
            )
            for facet in facets
        )
        return ProviderSearchResponse(
            [_project_paris_acquisition_formats(item) for item in self._decorate(
                rows, self.provider_id, self.credential_id
            )],
            projected,
            partial=bool(warnings),
        )

    def acquisition_offers(self, item: SearchResult) -> AcquisitionOfferSet:
        return _paris_acquisition_offer_set(item)

    def holdings(self, item: SearchResult, search_terms: str = "") -> ItemDetail:
        detail = self.client.holdings(item.docbase, item.record_id, item.scenario, search_terms)
        detail.source_provider = self.provider_id
        detail.source_credential = self.credential_id
        return detail

    def borrow(self, item: SearchResult, account: Account) -> Loan:
        """A legacy call has no persistent claim for the mutating redirect."""
        raise ProviderCapabilityError("paris_borrow_claim_required")

    def borrow_explicit_with_claim(
        self, item: SearchResult, account: Account, *,
        admission, claimed_receipt, confirmed=False, core_scope_current=None,
    ) -> Loan:
        """Manual PNB attempt under the same public claim ABI as BnF.

        Core must persist the exact claim before calling this method. Sync
        never calls it; an unavailable ACSM does not disprove an observed loan.
        """
        from cifereca_paris_borrow import borrow_with_claim
        return borrow_with_claim(
            self, item, account, admission=admission,
            claimed_receipt=claimed_receipt, confirmed=confirmed,
            core_scope_current=core_scope_current,
        )

    def renew(self, loan: Loan, account: Account) -> dict[str, Any]:
        # Provider-side defense: even a direct plugin call must never POST
        # RenewLoans for digital loans, another supplier or a foreign account.
        # Core still owns the fresh positive CanRenew check and at-most-once claim.
        if (type(loan) is not Loan or type(account) is not Account
                or self.provider_id != "paris"
                or loan.medium != "physical"
                or loan.provider != "SYRACUSE"
                or loan.renewable is not True
                or type(loan.raw) is not dict
                or loan.raw.get("CanRenew") is not True
                or type(loan.id) is not str or not loan.id
                or (loan.raw.get("Id") or loan.raw.get("HoldingId")) != loan.id
                or (loan.state is not None and (
                    type(loan.state) is not str or loan.state.casefold() in {
                        "returned", "closed", "expired", "cancelled", "canceled",
                    }
                ))
                or loan.source_provider != "paris"
                or loan.source_credential != self.credential_id
                or account.provider_id != self.provider_id
                or account.credential_id != self.credential_id
                or loan.source_account_id != (account.unique_id or "primary")
                or loan.identity_id != account.identity_id):
            raise ProviderCapabilityError("paris_renew_physical_only")
        return self.client.renew_loan(loan, "" if account.is_primary else account.unique_id)

    def digital_preferences(self, account: Account) -> dict[str, Any]:
        return self.client.pnb_preferences()

    def diagnostic_state(self) -> dict[str, Any]:
        state = super().diagnostic_state()
        state["pnb_borrow"] = dict(getattr(self.client, "last_pnb_borrow", {"phase": "unknown"}))
        state["account_status"] = dict(self.account_status)
        state["pairings"] = dict(self.pairing_status)
        state["history_errors"] = [
            dict(row) for row in getattr(self.client, "last_history_errors", ()) if isinstance(row, dict)
        ]
        # Observed response-page descriptors only. Never an active server
        # search filter or property attached to an individual book.
        state["search_response_facets"] = [
            dict(facet) for facet in getattr(self, "last_search_response_facets", ())
        ]
        return state

    def downloadable_resource_snapshot(self, account):
        """#3723 v2: exact source observations, not permission or delivery."""
        from cifereca.plugin_api import from_legacy_rights_snapshot
        return from_legacy_rights_snapshot(self.rights_observation_snapshot(account))

    def downloadable_resource_snapshot_from_loan_inventory(
        self, account, *, loan_inventory,
    ):
        """#4281: project the *same* exact completed loan read in opening Sync.

        Called only by the Core scoped lease. Never a durable cache: the
        ContextVar is restored even when validation/rights projection fails.
        All existing broker/owner/account and PNB-offer checks run unchanged.
        Ordinary rights, selected-offer, borrow and content delivery remain
        independently fresh, including after an upstream mutation.
        """
        if (type(account) is not Account
                or type(loan_inventory) is not LoanInventorySnapshot
                or loan_inventory.scope != LoanInventoryScope.for_account(account)
                or loan_inventory.status != "success"
                or not loan_inventory.branch_claims_complete("dilicom", "digital")
                or not loan_inventory.branch_claims_complete("syracuse", "physical")):
            raise ProviderCapabilityError("paris_same_cycle_inventory_unverified")
        from cifereca.plugin_api import from_legacy_rights_snapshot
        with self._loan_inventory_lock:
            marker = self._last_typed_loan_marker
            # Consume before rights projection, including on rejection. A
            # replay can never use this token, and independent rights reads
            # still perform two fresh ListLoans calls.
            self._last_typed_loan_marker = None
            matching = [
                live for live in self._accounts if type(live) is Account
                and LoanInventoryScope.for_account(live) == loan_inventory.scope
            ]
            if (marker is None or len(matching) != 1
                    or marker != (
                        loan_inventory.observed_at, loan_inventory.scope,
                        id(matching[0]), id(self._accounts),
                        getattr(self, "_epoch", None),
                    )
                    or self.connected is not True):
                raise ProviderCapabilityError("paris_same_cycle_inventory_stale")
            token = self._same_cycle_rights_inventory.set(loan_inventory)
            try:
                return from_legacy_rights_snapshot(
                    self.rights_observation_snapshot(account)
                )
            finally:
                self._same_cycle_rights_inventory.reset(token)

    def downloadable_selected_offer_snapshot(
        self, account, *, edition_id, resource_id, kind, branch,
        explicit_user_action=False,
    ):
        """#3723 v2: selected provider offer, rights state descriptive only."""
        from cifereca.plugin_api import from_legacy_rights_snapshot
        return from_legacy_rights_snapshot(
            self.rights_selected_delivery_offer_snapshot(
                account, edition_id=edition_id, right_id=resource_id,
                kind=kind, branch=branch,
                explicit_user_action=explicit_user_action,
            )
        )

    def downloadable_selected_delivery_candidate(
        self, account, *, edition_id, resource_id, kind, branch,
        selected_offer_id, target_dir, explicit_user_action=False,
    ):
        """Compatibility v2 candidate; explicit/legacy callers remain unchanged."""
        return self._downloadable_selected_delivery_candidate(
            account, edition_id=edition_id, resource_id=resource_id, kind=kind,
            branch=branch, selected_offer_id=selected_offer_id,
            target_dir=target_dir, explicit_user_action=explicit_user_action,
        )

    def downloadable_selected_delivery_candidate_guarded(
        self, account, *, edition_id, resource_id, kind, branch,
        selected_offer_id, target_dir, explicit_user_action=False,
        admission_current,
    ):
        """Core revocation fence for the whole selected Paris PNB transfer."""
        if not callable(admission_current) or not _paris_core_admission_current(admission_current):
            raise ProviderCapabilityError("paris_core_admission_revoked")
        return self._downloadable_selected_delivery_candidate(
            account, edition_id=edition_id, resource_id=resource_id, kind=kind,
            branch=branch, selected_offer_id=selected_offer_id,
            target_dir=target_dir, explicit_user_action=explicit_user_action,
            admission_current=admission_current,
        )

    def _downloadable_selected_delivery_candidate(
        self, account, *, edition_id, resource_id, kind, branch,
        selected_offer_id, target_dir, explicit_user_action=False,
        admission_current=None,
    ):
        """Stage the exact provider candidate, never a Core receipt or B."""
        from cifereca.plugin_api import SourceReference, DeliveryCandidate
        # Pin the caller scope *before* the underlying provider performs I/O.
        # A rebind must not manufacture a candidate for the new owner.
        scope = self._downloadable_scope(account)
        path, hint = self.rights_selected_delivery_candidate(
            account, edition_id=edition_id, right_id=resource_id,
            kind=kind, branch=branch, delivery_offer_id=selected_offer_id,
            target_dir=target_dir, explicit_user_action=explicit_user_action,
            **({"admission_current": admission_current}
               if admission_current is not None else {}),
        )
        # Compare the mutable caller against the frozen, previously valid
        # scope. Reconstructing ProviderRightsScope *after* GET could itself
        # raise on an empty/invalid account field, bypassing candidate cleanup.
        if ((account.identity_id, account.provider_id,
                account.credential_id, account.unique_id) != (
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id)
                or not _paris_core_admission_current(admission_current)):
            try:
                staged = Path(path)
                if (staged.parent == Path(target_dir) and staged.is_file()
                        and not staged.is_symlink()):
                    staged.unlink(missing_ok=True)
            except (OSError, TypeError, ValueError):
                pass
            raise ProviderCapabilityError("selected_delivery_scope_changed")
        return DeliveryCandidate(
            source=SourceReference(
                scope, branch,
                edition_id, resource_id, kind,
            ),
            selected_offer_id=selected_offer_id, path=Path(path),
            provider_hint=hint,
        )

    @staticmethod
    def _downloadable_scope(account):
        from cifereca.plugin_api import ProviderRightsScope
        return ProviderRightsScope(
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )

    def rights_selected_delivery_offer_snapshot(
        self, account: Account, *, edition_id: str, right_id: str,
        kind: str, branch: str, explicit_user_action: bool = False,
    ) -> ProviderRightsSnapshot:
        """Exact scoped PNB action; observational, never a content GET."""
        if (explicit_user_action is not True or type(account) is not Account
                or kind != "loan" or branch != "dilicom"
                or type(edition_id) is not str or not edition_id
                or type(right_id) is not str or not right_id
                or len(edition_id) > 256 or len(right_id) > 256):
            raise ProviderCapabilityError("paris_selected_offer_not_admitted")
        lease = self._current_source_sync_offer_lease(account)
        cached = lease.get("rights") if lease is not None else None
        if (type(cached) is ProviderRightsSnapshot
                and cached.status == "success"
                and cached.coverage == "complete"
                and cached.scope == self._downloadable_scope(account)):
            snapshot = cached
            _TRACE_LOG.info(
                "TRACE paris selected offer proof=previously_attested "
                "reobservation_attempted=false reason_code=same_cycle_exact",
            )
        else:
            snapshot = self.rights_observation_snapshot(account)
            _TRACE_LOG.info(
                "TRACE paris selected offer proof=fresh_observation "
                "reobservation_attempted=true reason_code=unscoped_or_unproven",
            )
        right = snapshot.exact(edition_id, right_id, "loan")
        if right is None or not right.provider_attested:
            # A failed or incomplete private inventory is not evidence that
            # this already-attested loan disappeared or was revoked.
            reason = ("paris_selected_offer_observation_unavailable"
                      if snapshot.coverage != "complete"
                      else "paris_selected_offer_unobserved")
            raise ProviderCapabilityError(reason)
        # State='unknown' is descriptive. The PNB action is independently
        # attested only when the fresh private ListLoans has EbookUrl.
        return replace(snapshot, rights=(right,))

    def rights_selected_delivery_candidate(
        self, account: Account, *, edition_id: str, right_id: str,
        kind: str, branch: str, delivery_offer_id: str,
        target_dir, explicit_user_action: bool = False,
        admission_current=None,
    ) -> tuple[Path, str]:
        """Reobserve the exact PNB link, then stage a file via the guarded GET.

        No PNB borrow/quota fallback: download_digital's selected-right mode
        requires the *current* matching DILICOM card and its direct EbookUrl.
        The result remains a candidate, not a Core receipt or OriginLink.
        """
        if (explicit_user_action is not True or type(account) is not Account
                or kind != "loan" or branch != "dilicom"
                or type(edition_id) is not str or not edition_id
                or type(right_id) is not str or not right_id
                or type(delivery_offer_id) is not str
                or not delivery_offer_id.startswith("paris.pnb:")
                or len(delivery_offer_id) != len("paris.pnb:") + 64
                or any(c not in "0123456789abcdef"
                       for c in delivery_offer_id[len("paris.pnb:"):])):
            raise ProviderCapabilityError("paris_candidate_not_admitted")
        if not _paris_core_admission_current(admission_current):
            raise ProviderCapabilityError("paris_core_admission_revoked")
        directory = Path(target_dir)
        try:
            if (not directory.is_dir() or directory.is_symlink()
                    or directory.resolve(strict=True) != directory.absolute()):
                raise ProviderCapabilityError("paris_candidate_staging_invalid")
        except (OSError, RuntimeError):
            raise ProviderCapabilityError("paris_candidate_staging_invalid") from None
        try:
            stage_fd = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
        except OSError:
            raise ProviderCapabilityError("paris_candidate_staging_invalid") from None
        try:
            observed = self.rights_selected_delivery_offer_snapshot(
                account, edition_id=edition_id, right_id=right_id,
                kind=kind, branch=branch, explicit_user_action=True,
            )
            selected = observed.exact(edition_id, right_id, "loan")
            if (selected is None or not selected.delivery_attested
                    or selected.delivery_offer_id != delivery_offer_id):
                raise ProviderCapabilityError("paris_candidate_offer_unavailable")
            # Preserve the same exact account/borrower binding as the observation.
            # A fully attested A→B Core source cycle can use its exact typed
            # inventory; independent/manual requests must do a fresh GET.
            # Existing download_digital and supplier host/token guards still
            # revalidate scope and candidate before *content* GET.
            with self._loan_inventory_lock:
                lease = self._current_source_sync_offer_lease(account)
                old_rights = lease.get("rights") if lease is not None else None
                old_inventory = lease.get("inventory") if lease is not None else None
                prior = (
                    old_rights.exact(edition_id, right_id, "loan")
                    if type(old_rights) is ProviderRightsSnapshot
                    else None
                )
                if (type(old_inventory) is LoanInventorySnapshot
                        and old_inventory.status == "success"
                        and old_inventory.scope == LoanInventoryScope.for_account(account)
                        and old_inventory.branch_claims_complete("dilicom", "digital")
                        and old_inventory.branch_claims_complete("syracuse", "physical")
                        and prior is not None and prior.delivery_attested
                        and prior.delivery_offer_id == delivery_offer_id):
                    inventory = old_inventory
                    _TRACE_LOG.info(
                        "TRACE paris candidate proof=previously_attested "
                        "reobservation_attempted=false reason_code=same_cycle_exact",
                    )
                else:
                    inventory = self._snapshot_for_account(account)
                    _TRACE_LOG.info(
                        "TRACE paris candidate proof=fresh_observation "
                        "reobservation_attempted=true reason_code=unscoped_or_unproven",
                    )
                if not _paris_core_admission_current(admission_current):
                    raise ProviderCapabilityError("paris_core_admission_revoked")
                candidates = []
                for loan in inventory.loans:
                    if (type(loan) is not Loan or loan.provider != "DILICOM"
                            or loan.medium != "digital"
                            or loan.id != right_id or loan.record_id != edition_id
                            or loan.source_provider != self.provider_id
                            or loan.source_credential != self.credential_id
                            or loan.source_account_id != (account.unique_id or "primary")
                            or loan.identity_id != account.identity_id
                            or type(loan.raw) is not dict
                            or not _pnb_direct_url_observed(loan.ebook_url, client=self.client)):
                        continue
                    raw_right = loan.raw.get("Id") or loan.raw.get("HoldingId")
                    raw_edition = loan.raw.get("RecordId")
                    if (type(raw_right) not in (str, int)
                            or type(raw_edition) not in (str, int)
                            or str(raw_right) != right_id
                            or str(raw_edition) != edition_id):
                        continue
                    candidates.append(loan)
                if len(candidates) != 1:
                    raise ProviderCapabilityError("paris_candidate_source_unobserved")
                # Another fresh ListLoans inside download_digital rejects a
                # disappeared link, rebind or loan mismatch before any PNB GET.
                pin = self._selected_delivery_inventory.set(inventory)
                try:
                    return self.download_digital(
                        candidates[0], account, directory,
                        expected_right_id=right_id,
                        expected_identity_id=account.identity_id,
                        selected_stage_fd=stage_fd,
                        **({"admission_current": admission_current}
                           if admission_current is not None else {}),
                    )
                finally:
                    self._selected_delivery_inventory.reset(pin)

        finally:
            os.close(stage_fd)

    def download_digital(
        self, loan: Loan, account: Account, target_dir: Path, *,
        expected_right_id: str | None = None,
        expected_identity_id: str | None = None,
        selected_stage_fd: int | None = None,
        admission_current=None, initial_offer_only: bool = False,
    ) -> tuple[Path, str]:
        """Existing-loan delivery only: exact live Paris borrower and loan.

        Core decides acquisition permission; this provider still must never
        consume a DILICOM PNB URL for a different live account or credential.
        The same lock protects the full private GET/staging against reconnect,
        and mutable objects/broker ownership are fenced after network I/O.
        """
        if initial_offer_only and not callable(admission_current):
            raise ProviderConfigurationError("paris_initial_acsm_scope_required")
        with self._loan_inventory_lock:
            if (not self.connected or type(account) is not Account
                    or type(loan) is not Loan
                    or account.provider_id != self.provider_id
                    or account.credential_id != self.credential_id
                    or type(account.is_primary) is not bool
                    or loan.provider != "DILICOM" or loan.medium != "digital"
                    or loan.source_provider != self.provider_id
                    or loan.source_credential != self.credential_id
                    or loan.source_account_id != (account.unique_id or "primary")
                    or (loan.identity_id is not None
                        and loan.identity_id != account.identity_id)
                    or not loan.id or not loan.record_id):
                raise ProviderConfigurationError("paris_download_scope_mismatch")

            scope = (account.unique_id, account.identity_id,
                     account.provider_id, account.credential_id,
                     account.is_primary)
            loan_scope = (
                loan.id, loan.record_id, loan.provider, loan.medium,
                loan.source_provider, loan.source_credential,
                loan.source_account_id, loan.identity_id,
                loan.ebook_url, loan.title,
            )
            live = [
                active for active in self._accounts
                if type(active) is Account
                and (active.unique_id, active.identity_id,
                     active.provider_id, active.credential_id,
                     active.is_primary) == scope
            ]
            if len(live) != 1:
                raise ProviderConfigurationError("paris_download_scope_mismatch")
            bound = live[0]
            broker = self.identity_broker

            def broker_binding():
                if broker is None:
                    return None
                try:
                    refs = broker.accounts(scope[1])
                    if type(refs) not in (tuple, list):
                        return None
                    key = (scope[0] if scope[0] else
                           f"{self.provider_id}:{self.credential_id}:primary")
                    owners = _core_account_owners(
                        refs, provider_id=self.provider_id,
                        account_id=key, credential_id=self.credential_id,
                    )
                    return (id(broker), owners) if owners is not None else None
                except Exception:
                    return None

            owner = broker_binding()
            if broker is not None and (owner is None or owner[1] != (scope[1],)):
                raise ProviderConfigurationError("paris_download_scope_mismatch")

            def scope_current():
                return (
                    _paris_core_admission_current(admission_current)
                    and self.connected is True
                    and (account.unique_id, account.identity_id,
                         account.provider_id, account.credential_id,
                         account.is_primary) == scope
                    and (bound.unique_id, bound.identity_id,
                         bound.provider_id, bound.credential_id,
                         bound.is_primary) == scope
                    and len(self._accounts) >= 1
                    and len(current := [
                        x for x in self._accounts if type(x) is Account
                        and (x.unique_id, x.identity_id,
                             x.provider_id, x.credential_id,
                             x.is_primary) == scope
                    ]) == 1 and current[0] is bound
                    and (loan.id, loan.record_id, loan.provider, loan.medium,
                         loan.source_provider, loan.source_credential,
                         loan.source_account_id, loan.identity_id,
                         loan.ebook_url, loan.title) == loan_scope
                    and self.identity_broker is broker
                    and (broker is None or broker_binding() == owner)
                )

            if not scope_current():
                raise ProviderConfigurationError("paris_download_scope_mismatch")
            # Canonical selected-right preflight is opt-in. A prior Core
            # right/consent admission must not silently reuse an old PNB URL
            # after return, renewal, account rebind or another user's copy.
            # Legacy explicit workflows without these arguments are intact.
            delivery_loan = replace(loan)
            if expected_right_id is not None or expected_identity_id is not None:
                if (type(expected_right_id) is not str
                        or not 0 < len(expected_right_id) <= 256
                        or expected_right_id != loan.id
                        or type(expected_identity_id) is not str
                        or expected_identity_id != scope[1]
                        or owner is None or owner[1] != (expected_identity_id,)):
                    raise ProviderConfigurationError("paris_selected_right_not_admitted")
                # This is a new authenticated loan *inventory* read, not a
                # new acquisition/token GET. It cannot prove complete
                # absence or turn an untrusted state into 'active'. Require
                # a single exact DILICOM right before touching any PNB URL.
                pinned = self._selected_delivery_inventory.get()
                if pinned is not None:
                    # The pinned read is only usable inside the candidate
                    # call under the same reentrant session lock. A changed
                    # account or incomplete upstream observation fails closed.
                    if (type(pinned) is not LoanInventorySnapshot
                            or pinned.scope != LoanInventoryScope.for_account(account)
                            or not pinned.branch_claims_complete("dilicom", "digital")):
                        raise ProviderConfigurationError("paris_selected_right_unobserved")
                    fresh = pinned
                else:
                    fresh = self._snapshot_for_account(account)
                if not scope_current():
                    raise ProviderConfigurationError("paris_download_scope_mismatch")
                failed_dilicom = any(
                    branch.upstream == "dilicom"
                    and branch.reason_code == "branch_unavailable"
                    for branch in fresh.branches
                )
                candidates = []
                for row in (() if failed_dilicom else fresh.loans):
                    if (type(row) is not Loan
                            or row.provider != "DILICOM"
                            or row.medium != "digital"
                            or row.id != expected_right_id
                            or row.record_id != loan.record_id
                            or row.source_provider != self.provider_id
                            or row.source_credential != self.credential_id
                            or row.source_account_id != (scope[0] or "primary")
                            or row.identity_id != scope[1]
                            or type(row.raw) is not dict):
                        continue
                    raw_id = row.raw.get("Id") or row.raw.get("HoldingId")
                    raw_edition = row.raw.get("RecordId")
                    if (type(raw_id) not in (str, int)
                            or type(raw_edition) not in (str, int)
                            or str(raw_id) != expected_right_id
                            or str(raw_edition) != loan.record_id):
                        continue
                    candidates.append(row)
                if len(candidates) != 1 or not scope_current():
                    raise ProviderConfigurationError("paris_selected_right_unobserved")
                # Only the FRESH account-scoped card supplies a candidate.
                # Without its explicit PNB link, the historical fallback
                # DownloadDigitalDocument may issue a quota/borrow POST:
                # never use that route for an exact Core-selected old right.
                # This is an attested candidate URL, NOT positive active
                # permission or the final delivery receipt.
                selected = candidates[0]
                if type(selected.ebook_url) is not str or not selected.ebook_url.strip():
                    raise ProviderConfigurationError("paris_selected_delivery_unobserved")
                delivery_loan = replace(selected)
            # Never pass the caller's mutable loan directly to the PNB
            # request builder: it reads provider/edition after preferences.
            # Its private POST must always use the edition admitted above.
            borrower_uid = "" if scope[4] else scope[0]
            completed, historical_url = self._download_digital_scoped_impl(
                delivery_loan, account, target_dir,
                scope_guard=scope_current, borrower_uid=borrower_uid,
                initial_offer_only=initial_offer_only,
                selected_offer_only=expected_right_id is not None,
                selected_stage_fd=selected_stage_fd,
                **({"stream_admission_current": scope_current}
                   if admission_current is not None else {}),
            )
            # A signed PNB URL is transient secret transport evidence, NOT a
            # SourceReference or Core delivery receipt. Keep the old tuple
            # contract for historical callers only; the selected canonical
            # route returns no private URL outside the provider boundary.
            return completed, ("" if expected_right_id is not None
                               else historical_url)

    def _download_digital_scoped_impl(self, loan: Loan, account: Account,
                                      target_dir: Path, *, scope_guard=None,
                                      borrower_uid=None,
                                      selected_stage_fd=None,
                                      stream_admission_current=None,
                                      initial_offer_only=False,
                                      selected_offer_only=False) -> tuple[Path, str]:
        """Network + validated staging, guarded by download_digital in service."""
        def ensure_scope():
            if scope_guard is not None and not scope_guard():
                raise ProviderConfigurationError("paris_download_scope_mismatch")

        started = time.monotonic()
        # Book metadata may be included only when the catalogue identifier is
        # an exact public 13-digit EAN. Borrower and loan identifiers stay private.
        book_id = public_book_id(loan.record_id)

        # Active DILICOM loans already carry their PNB delivery URL in
        # AdditionalProperties.EbookUrl.  That URL represents the existing
        # right and must be preferred over Syracuse DownloadDigitalDocument,
        # which can re-apply borrowing/quota business rules to an already
        # active loan.
        direct_url = str(loan.ebook_url or "").strip()
        if direct_url:
            url = urljoin(self.client.base_url + "/", direct_url)
            parsed_direct = urlparse(url)
            if parsed_direct.scheme not in {"http", "https"}:
                raise ProviderConfigurationError("Lien PNB du prêt invalide: schéma HTTP(S) requis")
            _TRACE_LOG.info(
                "TRACE download paris direct-loan-url book_id=%s site=%s",
                book_id, public_site(url),
            )
        else:
            if initial_offer_only:
                # A first-borrow token GET can never create/extend a loan.
                # Missing one-shot URL means no candidate, not a fallback POST.
                raise ProviderConfigurationError("paris_initial_acsm_url_missing")
            _TRACE_LOG.info(
                "TRACE download paris direct-loan-url missing book_id=%s fallback=DownloadDigitalDocument",
                book_id,
            )
            _TRACE_LOG.info(
                "TRACE download paris prefs begin book_id=%s upstream_uid_present=%s",
                book_id, bool(account.unique_id),
            )
            ensure_scope()
            prefs = self.client.pnb_preferences()
            ensure_scope()
            _TRACE_LOG.info(
                "TRACE download paris prefs end book_id=%s reader_id_present=%s passphrase_present=%s hint_present=%s duration_ms=%s",
                book_id, bool(prefs.get("ReaderId")), bool(prefs.get("PassPhrase")),
                bool(prefs.get("Hint")), int((time.monotonic()-started)*1000),
            )
            endpoint_started = time.monotonic()
            _TRACE_LOG.info(
                "TRACE download paris acquisition-url begin book_id=%s site=%s upstream_uid_present=%s",
                book_id, public_site(self.client.base_url), bool(account.unique_id),
            )
            ensure_scope()
            url = self.client.download_digital_document(
                loan,
                borrower_uid if borrower_uid is not None else
                ("" if account.is_primary else account.unique_id),
                prefs,
            )
            ensure_scope()
            _TRACE_LOG.info(
                "TRACE download paris acquisition-url end book_id=%s url_returned=%s duration_ms=%s",
                book_id, bool(url), int((time.monotonic()-endpoint_started)*1000),
            )
            url = urljoin(self.client.base_url + "/", url)
        parsed = urlparse(url)
        http_started = time.monotonic()
        _TRACE_LOG.info(
            "TRACE download paris acsm-http begin book_id=%s site=%s",
            book_id, public_site(url),
        )
        current = url
        visited_urls: set[str] = set()
        for hop in range(12):
            ensure_scope()
            mutating_endpoint = _paris_mutating_pnb_endpoint(current)
            if initial_offer_only and (mutating_endpoint or not direct_url):
                # The first borrow already consumed its mutating endpoint.
                # Neither ASSARedirect nor DownloadDigitalDocument is safe to
                # replay, even behind multiple layers of percent-encoding.
                raise ProviderConfigurationError("paris_initial_acsm_replay_denied")
            self.client._validate_pnb_redirect_url(current)
            if selected_offer_only and mutating_endpoint:
                # A selected, ALREADY existing right can never traverse a
                # lender's borrow/quota re-evaluation endpoint. Check each
                # qualified redirect target BEFORE its next network request,
                # not only the initial URL. The first-offer staged receipt
                # is consumed separately by Core without any second GET.
                raise ProviderConfigurationError(
                    "paris_selected_delivery_mutating_redirect_denied")
            if current in visited_urls:
                # The signed URL may carry borrower tokens: do not re-GET or log it.
                raise ProviderConfigurationError("Boucle de redirections PNB détectée")
            visited_urls.add(current)
            hop_started = time.monotonic()
            response = self.client._get_pnb_response(
                current,
                **({"admission_current": stream_admission_current}
                   if stream_admission_current is not None else {}),
            )
            ensure_scope()
            self.client._pnb_trace_response(response, hop=hop + 1, elapsed_ms=int((time.monotonic()-hop_started)*1000))
            if initial_offer_only and response.status_code in {301, 302, 303, 307, 308}:
                # The one-shot post-borrow read is never allowed to follow an
                # unqualified redirect: its destination could be another
                # supplier mutation endpoint. Keep the loan confirmed; the
                # separately staged ACSM is simply unavailable.
                raise ProviderConfigurationError("paris_initial_acsm_redirect_unqualified")
            if response.status_code not in {301,302,303,307,308}: break
            location = response.headers.get('location')
            if not location: raise ProviderConfigurationError('Redirection PNB sans Location')
            current = urljoin(str(response.url), location)
        else: raise ProviderConfigurationError('Trop de redirections PNB')
        _TRACE_LOG.info(
            "TRACE download paris acsm-http response book_id=%s status=%s content_type=%s bytes=%s site=%s duration_ms=%s",
            book_id, public_http_status(response.status_code),
            public_content_type(response.headers.get("content-type", "")),
            len(response.content), public_site(str(response.url)),
            int((time.monotonic()-http_started)*1000),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            # HTTPX messages include the full signed response URL. Preserve
            # only a closed numeric status in provider diagnostics.
            raise ProviderConfigurationError(
                f"PNB HTTPS livraison HTTP {public_http_status(response.status_code)}"
            ) from None
        content = response.content
        content_type = response.headers.get("content-type", "")
        fmt = 'pdf' if content.startswith(b'%PDF-') else ('epub' if content.startswith(b'PK') else 'acsm')
        inspected = inspect_artifact_content(content, fmt, strict_structure=True)
        ensure_scope()
        if not inspected.valid:
            labels = {
                "pdf": "PDF invalide ou tronqué",
                "epub": "EPUB invalide ou tronqué",
                "acsm": "ACSM invalide ou réponse HTML",
            }
            raise ProviderError(labels.get(fmt, "Format numérique non pris en charge"))
        _TRACE_LOG.info("TRACE download paris document-validated book_id=%s kind=%s bytes=%s", book_id, fmt, len(content))
        name = sanitize_filename_component(str(loan.title), "download")
        path = target_dir / (name[:160] + '.' + fmt)
        ensure_scope()
        if selected_stage_fd is None:
            # Preserve the explicitly requested historical/manual behavior.
            publish_artifact_content(path, content)
        else:
            from cifereca_paris_selected_staging import save_selected_candidate
            path = save_selected_candidate(target_dir, selected_stage_fd,
                                           path.name, content)
        ensure_scope()
        _TRACE_LOG.info(
            "TRACE download paris acsm-write book_id=%s bytes=%s detected_acsm=%s duration_ms=%s",
            book_id, len(content), path.suffix.casefold() == ".acsm",
            int((time.monotonic()-started)*1000),
        )
        return path, url


class ParisProviderPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self, context):
        self._identity_broker = context.broker("identities")
        return self

    def deactivate(self):
        self._identity_broker = None

    def create_session(self, *, credential_id: str, label: str, credentials: dict[str, Any], options: dict[str, Any], config: Any, storage: Any, diagnostics: Any) -> ProviderSession:
        return ParisProviderSession(
            credential_id=credential_id,
            label=label,
            credentials=credentials,
            options=options,
            config=config,
            diagnostics=diagnostics,
            identity_broker=getattr(self, "_identity_broker", None),
        )


PLUGIN = ParisProviderPlugin()
