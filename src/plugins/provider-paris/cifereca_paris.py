from __future__ import annotations
from cifereca_paris_formats import explicit_formats

import logging
import httpx
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
import re
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from cifereca.plugin_api import (
    Account, Booking, HistoryItem, ItemDetail, Loan, SearchResult,
    AcquisitionOffer, AcquisitionOfferSet, AcquisitionPreferences,
    acquisition_format_projection, normalize_acquisition_format,
)
from cifereca_paris_client import AuthenticationError, ParisClient, ParisSummaryHTTPError, ParisLoanListError, ParisError
from cifereca_paris_trace import (
    public_book_id, public_content_type, public_http_status, public_site,
)
from cifereca.plugin_api import (
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



DESCRIPTOR = ProviderDescriptor(
    id="paris",
    display_name="Bibliothèques de Paris",
    description="Catalogue, compte lecteur et prêt numérique PNB des Bibliothèques de Paris.",
    state="ready",
    capabilities=ProviderCapabilities(
        authentication=True,
        multiple_credentials=True,
        linked_accounts=True,
        search=True,
        loans=True,
        history=True,
        bookings=True,
        holdings=True,
        borrow=True,
        digital_download=True,
        # Preserve the existing authorized Paris opening-sync path only.
        # Newly installed lenders remain default-deny until explicit opt-in.
        opening_loan_auto_acquisition=True,
        return_loan=True,
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


class ParisProviderSession(ProviderSession):
    def __init__(self, *, config: Any, diagnostics: Any,
                 identity_broker=None, **kwargs: Any) -> None:
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self.identity_broker = identity_broker
        base_url = str(self.options.get("base_url") or (config.paris_base_url(self.credential_id) if hasattr(config, "paris_base_url") else config.get("base_url", "https://bibliotheques.paris.fr")))
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
        self.last_loan_errors: list[dict[str, str]] = []
        self.account_status: dict[str, Any] = {"usable": None, "reason": None}
        self.pairing_status: dict[str, Any] = {"complete": None, "error": None}
        self._loan_catalog_metadata_cache: dict[str, dict[str, Any]] = {}

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
        # DILICOM (digital/PNB) and SYRACUSE (physical) are independent upstream
        # services. One transient failure must not hide all loans from the other.
        rows: list[Loan] = []
        errors: list[dict[str, str]] = []
        for upstream in ("DILICOM", "SYRACUSE"):
            try:
                rows.extend(self.client.loans(upstream, account.unique_id))
            except Cancelled:
                # Cooperative cancellation is NOT an unavailable branch.
                # Stop the second upstream GET and never publish a partial
                # inventory or synthetic negative-rights evidence.
                raise
            except Exception as exc:
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
        # Do not let a later mutation of the legacy error facade change the
        # branch coverage of this particular typed read.
        self._loan_branch_failures.set(frozenset(error["upstream"] for error in errors))
        self.last_loan_errors = errors
        if errors and not rows and len(errors) == 2:
            raise ProviderConfigurationError(
                "Bibliothèques de Paris : inventaires DILICOM et SYRACUSE indisponibles"
            ) from None
        rows = self._decorate(rows, self.provider_id, self.credential_id)
        for loan in rows:
            self._enrich_digital_loan_metadata(loan)
        # Persist the exact borrower on each loan. Paris uses the empty string
        # for the primary account, which must not be confused with "unknown".
        for loan in rows:
            loan.source_account_id = account.unique_id or "primary"
        return sorted(rows, key=lambda x: (x.due_at is None, x.due_at or "", x.title.casefold()))

    def loan_inventory_snapshot(self, account: Account) -> LoanInventorySnapshot:
        with self._loan_inventory_lock:
            return self._snapshot_for_account(account)

    def _snapshot_for_account(self, account: Account) -> LoanInventorySnapshot:
        """One scoped legacy read with separate DILICOM/SYRACUSE evidence.

        Neither legacy Paris upstream currently attests pagination/exhaustive
        absence. Consequently even an all-successful read claims UNKNOWN
        completeness, never proof that a missing loan was returned.
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
        # A typed snapshot never interprets last_loan_errors: that v1 public
        # side channel can be overwritten by legacy consumers or wrappers.
        token = self._loan_branch_failures.set(None)
        try:
            rows = self.loans(account)
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
            LoanBranchCoverage(
                upstream=upstream.casefold(), medium=medium,
                completeness="partial" if upstream in failed else "unknown",
                reason_code="branch_unavailable" if upstream in failed else None,
            )
            for upstream, medium in (
                ("DILICOM", "digital"), ("SYRACUSE", "physical")
            )
        )
        return LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(account),
            loans=normalized,
            status="partial" if failed else "success",
            branches=branches,
            reason_code="branch_unavailable" if failed else None,
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
                    refs = broker.accounts()
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
                    owners = [
                        ref.identity_id if type(ref.identity_id) is str else ""
                        for ref in refs
                        if (getattr(ref, "provider_id", None) == self.provider_id
                            and getattr(ref, "account_id", None) == core_account_id)
                    ]
                    return (id(broker), tuple(sorted(owners)))
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
            inventory = self._snapshot_for_account(account)
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
                        or type(edition_id) not in (str, int)
                        or not str(right_id) or not str(edition_id)
                        or loan.id != str(right_id)
                        or loan.record_id != str(edition_id)):
                    continue
                # The same provider loan ID on two upstream cards is an
                # ambiguous right, even when both cards cite one edition.
                # Exclude *both* occurrences without invalidating other
                # attested DILICOM rights in this partial inventory.
                right_key = str(right_id)
                if right_key in ambiguous_right_ids:
                    continue
                if right_key in rights_by_id:
                    rights_by_id.pop(right_key)
                    ambiguous_right_ids.add(right_key)
                    continue
                rights_by_id[right_key] = ProviderRightObservation(
                    edition_id=str(edition_id), right_id=right_key,
                    kind="loan", state="unknown",
                    evidence_kind="provider_response",
                    evidence_ref="paris.dilicom.listloans",
                    observed_at=observed, delivery="unknown",
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
            return ProviderRightsSnapshot(
                scope=ProviderRightsScope(
                    scope.identity_id, scope.provider_id,
                    scope.credential_id, scope.account_id,
                ),
                rights=tuple(rights_by_id.values()),
                status="failed" if dilicom_failed else "partial",
                coverage="unknown" if dilicom_failed else "partial",
                pagination_complete=False, branch="dilicom",
                observed_at=observed, snapshot_ref="paris.dilicom.listloans",
            )

    def history(self, account: Account) -> list[HistoryItem]:
        return self._decorate(self.client.all_history(account.unique_id), self.provider_id, self.credential_id)

    def bookings(self, account: Account) -> list[Booking]:
        return self._decorate(self.client.all_bookings(account.unique_id), self.provider_id, self.credential_id)

    def search(self, query: str, scope: str = "all", page: int = 0, field: str = "all") -> list[SearchResult]:
        # An unsupported field/scope is a new failed attempt, not a reason
        # to keep a previous partial Syracuse branch warning visible.
        self.last_search_warnings = []
        self.last_search_response_facets = []
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
        scope = scope.strip().casefold()
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

    def acquisition_offers(self, item: SearchResult) -> AcquisitionOfferSet:
        return _paris_acquisition_offer_set(item)

    def holdings(self, item: SearchResult, search_terms: str = "") -> ItemDetail:
        detail = self.client.holdings(item.docbase, item.record_id, item.scenario, search_terms)
        detail.source_provider = self.provider_id
        detail.source_credential = self.credential_id
        return detail

    def borrow(self, item: SearchResult, account: Account) -> Loan:
        """Create a real Paris PNB loan using the captured official redirect flow.

        The captured contract is valid for the primary account only.  Paris uses
        the empty upstream UID for that account; linked/delegated accounts remain
        intentionally rejected until their mutation flow is captured.
        """
        uid = "" if account.is_primary or not account.unique_id else account.unique_id
        loan = self.client.borrow_digital(item, uid)
        loan.source_provider = self.provider_id
        loan.source_credential = self.credential_id
        loan.source_account_id = account.unique_id or "primary"
        self._enrich_digital_loan_metadata(loan)
        return loan

    def renew(self, loan: Loan, account: Account) -> dict[str, Any]:
        return self.client.renew_loan(loan, "" if account.is_primary else account.unique_id)

    def return_digital(self, loan: Loan, account: Account) -> dict[str, Any]:
        return self.client.return_digital_loan(loan, "" if account.is_primary else account.unique_id)

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
        state["booking_errors"] = [
            dict(row) for row in getattr(self.client, "last_booking_errors", ()) if isinstance(row, dict)
        ]
        # Observed response-page descriptors only. Never an active server
        # search filter or property attached to an individual book.
        state["search_response_facets"] = [
            dict(facet) for facet in getattr(self, "last_search_response_facets", ())
        ]
        return state

    def download_digital(self, loan: Loan, account: Account, target_dir: Path) -> tuple[Path, str]:
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
            _TRACE_LOG.info(
                "TRACE download paris direct-loan-url missing book_id=%s fallback=DownloadDigitalDocument",
                book_id,
            )
            _TRACE_LOG.info(
                "TRACE download paris prefs begin book_id=%s upstream_uid_present=%s",
                book_id, bool(account.unique_id),
            )
            prefs = self.client.pnb_preferences()
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
            url = self.client.download_digital_document(loan, "" if account.is_primary else account.unique_id, prefs)
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
        for hop in range(12):
            self.client._validate_pnb_redirect_url(current)
            hop_started = time.monotonic()
            response = self.client._get_pnb_response(current)
            self.client._pnb_trace_response(response, hop=hop + 1, elapsed_ms=int((time.monotonic()-hop_started)*1000))
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
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "")
        fmt = 'pdf' if content.startswith(b'%PDF-') else ('epub' if content.startswith(b'PK') else 'acsm')
        inspected = inspect_artifact_content(content, fmt, strict_structure=True)
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
        publish_artifact_content(path, content)
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
