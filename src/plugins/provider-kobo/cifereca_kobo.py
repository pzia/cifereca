from __future__ import annotations

import logging
import re
from typing import Any

from cifereca_kobo_vault import KoboVaultClient, KoboVaultError
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_policy import (
    classify_personal_manifest, _strict_account_scope, _strict_product_id,
    _SCOPE_DENIED,
)
from cifereca.plugin_api import Account, ItemDetail, SearchResult, AcquisitionOffer, CredentialField, ProviderCapabilities, ProviderSearchCapabilities, ProviderCapabilityError, ProviderDescriptor, ProviderError, ProviderPlugin, ProviderSession


_TRACE_LOG = logging.getLogger("cifereca")
_UNSET_PERSONAL_IDENTITY = object()  # None is a legitimate legacy identity.

# Only protocol metadata with a known public vocabulary may reach diagnostics.
# The public /status endpoint is still an untrusted HTTP response.
_PUBLIC_VAULT_CAPABILITIES = frozenset({
    "catalog_search", "library_search", "library", "acquisition",
})
_PUBLIC_ACQUISITION_ACCESS = frozenset({
    "configured", "unknown", "available", "disabled", "unavailable",
    "denied", "forbidden", "requires_scope",
})


def _vault_public_version(value: Any) -> str:
    if type(value) is not str:
        return ""
    value = value.strip()
    return value if re.fullmatch(r"v?[0-9]{1,3}(?:\.[0-9]{1,4}){1,4}", value) else ""


def _vault_account_id(account: Any) -> str | None:
    """Identify one authenticated account, never invent a fallback identity.

    Vault /account may carry both old and new ID keys. Reject conflicting,
    malformed or missing values before Core persists an account target.
    """
    if type(account) is not dict:
        return None
    values = []
    for key in ("id", "account_id"):
        value = account.get(key)
        if value is None or value == "":
            continue
        if (
            type(value) is not str or not 1 <= len(value) <= 256
            or any(not 33 <= ord(ch) <= 126 for ch in value)
        ):
            return None
        values.append(value)
    if not values or any(value != values[0] for value in values[1:]):
        return None
    return values[0]


def _vault_account_label(account: dict[str, Any]) -> str:
    """Present only bounded printable text, never arbitrary response objects."""
    for key in ("display_name", "name", "email_hint"):
        value = account.get(key)
        if type(value) is not str:
            continue
        name = value.strip()
        if (
            name and len(name) <= 120
            and all(
                ord(ch) >= 32 and ord(ch) != 127
                and not 0x80 <= ord(ch) <= 0x9F
                and ord(ch) != 0x061C
                and ord(ch) not in (0x200E, 0x200F)
                and not 0x202A <= ord(ch) <= 0x202E
                and not 0x2066 <= ord(ch) <= 0x2069
                and not 0xD800 <= ord(ch) <= 0xDFFF
                for ch in name
            )
        ):
            return name
    return "Compte Kobo"



# Provider diagnostics are exported through Core and may appear in support
# bundles. Even a substitute Vault/catalogue adapter is not trusted to supply
# safe rejection texts, keys, or account-scoped reason values.
_PUBLIC_REJECTION_TYPES = frozenset({"invalid_shape", "invalid_scope", "ProviderError"})
_PUBLIC_REJECTION_REASONS = frozenset({
    "unbound_account", "different_account", "ambiguous_account_binding",
    "duplicate_product_id", "missing_product_id", "unverifiable_product_id",
    "tainted_product_id", "item_not_object", "items_not_list",
    "normalization_error", "personal_search_denied",
})
_PUBLIC_REJECTION_SOURCES = frozenset({"catalog", "library"})
_MAX_PUBLIC_REJECTIONS = 256


def _public_kobo_rejections(rows: Any) -> list[dict[str, str]]:
    """Only publish fixed diagnostic codes, never arbitrary upstream fields."""
    if rows is None:
        return []
    if type(rows) not in (tuple, list):
        return [{"type": "invalid_shape", "reason": "unverified_rejection_list"}]
    published: list[dict[str, str]] = []
    for row in rows[:_MAX_PUBLIC_REJECTIONS]:
        if type(row) is not dict:
            published.append({
                "type": "invalid_shape", "reason": "unverified_provider_rejection",
            })
            continue
        category = row.get("type")
        reason = row.get("reason")
        source = row.get("source")
        published_row = {
            "type": (
                category if type(category) is str
                and category in _PUBLIC_REJECTION_TYPES else "invalid_shape"
            ),
            "reason": (
                reason if type(reason) is str
                and reason in _PUBLIC_REJECTION_REASONS
                else "unverified_provider_rejection"
            ),
        }
        if type(source) is str and source in _PUBLIC_REJECTION_SOURCES:
            published_row["source"] = source
        published.append(published_row)
    if len(rows) > _MAX_PUBLIC_REJECTIONS:
        published.append({
            "type": "invalid_shape", "reason": "truncated_rejections",
        })
    return published


def _safe_cover_count(items: Any, cover_url: Any) -> int:
    """Advisory thumbnail statistics cannot invalidate a rights inventory.

    Each Vault row is independent. Malformed nested cover fields or a custom
    adapter raising from its extractor must not abort all other account rows.
    Only the integer count is logged; never the private provider payload.
    """
    counted = 0
    for row in items:
        if type(row) is not dict:
            continue
        try:
            cover = cover_url(row)
        except Exception:
            continue
        if type(cover) is str and cover:
            counted += 1
    return counted


DESCRIPTOR = ProviderDescriptor(
    id="kobo",
    display_name="Kobo",
    description="Catalogue et bibliothèque Kobo via Kobo-Vault (API 2.3, révision 2.3.3 / schema acquisition 1.4).",
    state="ready",
    capabilities=ProviderCapabilities(
        authentication=True,
        multiple_credentials=True,
        search=True,
        holdings=True,
        personal_library=True,
        backup=True,
    ),
    search_capabilities=ProviderSearchCapabilities(
        # Vault accepts arbitrary 'field' as a query parameter, but the
        # deployed server-side semantics for specific fields are not proven.
        # Until verified, never advertise an exact author/ISBN/etc filter.
        fields=("all",),
        scopes=("all", "digital", "catalog"),
        pagination="page",
        page_size=50,
        max_pages=101,
        notes=(
            "Recherche générale via Kobo-Vault ; filtrage serveur auteur/ISBN non attesté.",
            "Sans compte attesté, all/digital restent limités au catalogue public.",
            "Avec un compte attesté, all/digital peuvent inclure la bibliothèque privée.",
            "catalog force exclusivement la projection publique sans droits inférés.",
        ),
    ),
    credential_fields=(CredentialField("api_token", "Token API Kobo-Vault", secret=True),),
    notes=(
        "Kobo-Vault est consommé comme fournisseur Library indépendant.",
        "cifereca résout l'acquisition via Vault puis délègue l'exécution locale au contrat public Kobo-Adept.",
    ),
)


class KoboSession(ProviderSession):
    """Projection of Kobo-Vault API 2.3+ into Paris' Provider model."""

    def __init__(self, *, config: Any, storage: Any, diagnostics: Any, catalog_factory=None, runtime_capabilities=None, identity_broker=None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.config = config
        self.storage = storage
        self.diagnostics = diagnostics
        self.runtime_capabilities = runtime_capabilities
        self.identity_broker = identity_broker
        base_url = str(self.options.get("base_url") or "").strip()
        token = str(self.credentials.get("api_token") or "").strip()
        timeout = float(self.options.get("timeout_seconds") or (config.request_timeout_seconds("kobo", self.credential_id) if hasattr(config, "request_timeout_seconds") else config.get("request_timeout_seconds", 25)))
        self.client = KoboVaultClient(base_url, token, timeout=timeout)
        # Production receives the dependency selected by PluginManager.
        # Direct construction is retained for callers exercising a session alone.
        if catalog_factory is None:
            self.catalog = KoboCatalogService(self.client, credential_id=self.credential_id, provider_id=self.provider_id)
        else:
            self.catalog = catalog_factory.create(self.client, credential_id=self.credential_id, provider_id=self.provider_id)
        self.capability_names: set[str] = set()
        self.capability_details: dict[str, dict[str, Any]] = {}
        self.api_version = ""
        self.vault_release = ""
        self.acquisition_access = "unknown"
        self.last_library_rejections: list[dict[str, str]] = []
        # A personal manifest response belongs to the connection generation
        # that started its GET, even when an A→B→A reconnect returns the same
        # account identifier. This guard does not infer any negative rights.
        self._personal_binding_generation = 0
        # Sanitized integrity signal for Core backup/preview. A personal
        # manifest with discarded upstream rows must never authorize pruning.
        self.backup_manifest_rejection_count = 0
        self.backup_manifest_policy = classify_personal_manifest(
            (), account_id=None,
        )
        profile = str(self.options.get("display_profile") or config.get("kobo_backup.display_profile", "Android") or "Android").strip()
        self.display_profile = profile if profile in {"Android", "Desktop"} else "Android"
        self.supports_acquisition_display_profile = False
        self.supports_acquisition_acsm = False

    def _diagnostic(self, *args: Any, **kwargs: Any) -> None:
        try:
            self.diagnostics.add(*args, **kwargs)
        except Exception:
            pass

    def connect(self) -> list[Account]:
        self._personal_binding_generation += 1
        # A reconnect may use a replaced token, so the previous successful
        # account handshake must not continue to authorize operations while
        # /status or /account is being queried. Keep the previous account
        # binding as provenance, but never keep the session ACTIVE on failure.
        # The delegated-scope denial is preserved until a verified success.
        self.connected = False
        self.capability_names = set()
        self.capability_details = {}
        self.supports_acquisition_acsm = False
        self.supports_acquisition_display_profile = False
        self.backup_manifest_policy = classify_personal_manifest((), account_id=None)
        status = self.client.status(authenticated=False)
        self.api_version = _vault_public_version(status.get("api_version") or status.get("version"))
        self.vault_release = _vault_public_version(status.get("release"))
        try:
            parts = self.api_version.removeprefix("v").split(".")
            # The version validator explicitly accepts "v2.3", but the
            # compatibility guard must parse that same legal spelling.
            major = int(parts[0]) if parts else 0
            minor = int(parts[1]) if len(parts) > 1 else 0
        except (TypeError, ValueError):
            major = minor = 0
        if major != 2 or minor < 3:
            shown = self.api_version or "inconnue"
            raise ProviderError(f"Kobo-Vault : version API incompatible ({shown}, v2.3+ requise)")
        caps = status.get("capabilities")
        if isinstance(caps, list):
            self.capability_names = {
                x for x in caps
                if type(x) is str and x in _PUBLIC_VAULT_CAPABILITIES
            }
        details = status.get("capability_details")
        if isinstance(details, list):
            for row in details:
                if not isinstance(row, dict):
                    continue
                key = row.get("id")
                if type(key) is not str or key not in _PUBLIC_VAULT_CAPABILITIES:
                    continue
                safe = {"id": key}
                for flag in ("available", "enabled"):
                    if type(row.get(flag)) is bool:
                        safe[flag] = row[flag]
                schema = _vault_public_version(row.get("schema_version"))
                if schema:
                    safe["schema_version"] = schema
                self.capability_details[key] = safe
        access = status.get("acquisition_access")
        self.acquisition_access = (
            access if type(access) is str and access in _PUBLIC_ACQUISITION_ACCESS
            else "unknown"
        )

        runtime = (
            self.runtime_capabilities.acquisition("kobo")
            if self.runtime_capabilities is not None
            else None
        )
        runtime_modes = set(getattr(runtime, "modes", ()) or ())
        vault_acquisition = "acquisition" in self.capability_names
        self.supports_acquisition_acsm = (
            vault_acquisition and "adobe_adept" in runtime_modes
        )
        self.supports_acquisition_display_profile = (
            vault_acquisition
            and bool(runtime_modes & {"kobo_delegated", "kobo_kdrm"})
        )
        account = self.client.account()
        account_id = _vault_account_id(account)
        if account_id is None:
            # No synthetic "kobo" ID and no coercion of arbitrary upstream
            # objects to strings: an ambiguous account cannot own files.
            raise ProviderError(
                "Kobo-Vault : identité du compte absente ou ambiguë"
            )
        # A token switch or a stale upstream cache must not silently rebind
        # an existing provider session to a different account. Core owns the
        # persisted identity/credential mapping; a new account needs a new
        # explicit connection rather than inheriting its ledger and 403 state.
        if self._accounts and (
            len(self._accounts) != 1
            or self._accounts[0].unique_id != account_id
        ):
            # Unlike an unavailable status endpoint, a confirmed account
            # SWITCH invalidates the previous session binding. Never leave
            # this object usable as the old account with the new token.
            self.connected = False
            raise ProviderError(
                "Kobo-Vault : identité du compte différente ; "
                "reconfiguration explicite requise"
            )
        display = _vault_account_label(account)
        # Reauthorization only takes effect after a *validated* authenticated
        # account identity, not when connect() was merely invoked. A malformed
        # /account response must not clear a prior delegated-scope denial.
        self._cifereca_delegation_forbidden = False
        self._cifereca_delegation_attempts_saved = 0
        self.connected = True
        self._accounts = [Account(
            display_name=display,
            unique_id=account_id,
            is_primary=True,
            provider_id=self.provider_id,
            credential_id=self.credential_id,
            provider_label=self.label or DESCRIPTOR.display_name,
        )]
        self._diagnostic(
            "KOBO", "vault connected", "ok",
            detail={
                "credential_configured": bool(self.credential_id),
                "api_version": self.api_version or "inconnue",
                "capabilities": sorted(self.capability_names),
                "role": "kobo-library-provider",
                "acquisition_access": self.acquisition_access,
                "provider_contract": f"v{self.api_version or '2.3'}",
                "vault_release": self.vault_release or None,
                "display_profile": self.display_profile if self.supports_acquisition_display_profile else None,
                "acsm_export": self.supports_acquisition_acsm,
            },
        )
        return self.accounts

    def close(self) -> None:
        self._personal_binding_generation += 1
        self.connected = False
        self.client.close()
        self.backup_manifest_policy = classify_personal_manifest((), account_id=None)

    def cover_url(self, item: dict[str, Any]) -> str | None:
        return self.catalog.cover_url(item)

    def normalize_item(self, item: dict[str, Any], *, default_collection: str = "catalog") -> SearchResult:
        return self.catalog.normalize_item(item, default_collection=default_collection)

    def search(self, query: str, scope: str = "all", page: int = 0, field: str = "all") -> list[SearchResult]:
        # Each attempt, including a rejected field or scope, supersedes any
        # earlier partial-results warning from an authenticated Vault search.
        self.last_search_warnings = []
        # The host uses all/digital/physical; catalog is also exposed for
        # explicitly public discovery. Unknown or malformed scopes must not
        # silently become an authenticated all-sources search and issue a
        # private /library/search request. Keep diagnostics independent of
        # the rejected caller value (which could contain a signed URL).
        if type(field) is not str or field.strip().casefold() != "all":
            # Never forward an unverified field query to the public or private
            # Vault routes, nor present general results as exact field matches.
            raise ProviderCapabilityError("Kobo-Vault : unsupported_field")
        if type(scope) is not str or scope not in {
            "all", "digital", "catalog",
        }:
            if scope == "physical":
                raise ProviderCapabilityError("Kobo-Vault : unsupported_scope")
            raise ProviderError("Kobo-Vault : portée de recherche invalide")
        if type(page) is not int or not 0 <= page <= 100:
            # Never send malformed pagination to a Vault catalogue route,
            # especially not to the separate authenticated /library/search.
            raise ProviderCapabilityError("Kobo-Vault : invalid_page")
        # Determine whether personal discovery is authorized *before* the
        # service can issue /library/search. Post-filtering private hits is
        # insufficient: a disconnected or mismatched account must not incur
        # a private HTTP request, nor be blocked by a personal 429/5xx.
        bound_account = (
            self._accounts[0] if self.connected and len(self._accounts) == 1
            else None
        )
        binding_generation = self._personal_binding_generation
        account_id = (
            bound_account.unique_id
            if bound_account is not None
            and bound_account.provider_id == self.provider_id
            and bound_account.credential_id == self.credential_id
            # A connected session with an invalid account key cannot attest
            # legacy library hits that omit an explicit account_id.
            and _strict_account_scope(bound_account.unique_id)
            else None
        )
        # Personal catalogue discovery can include rights-scoped rows; public
        # catalog-only requests never require an authenticated account.
        personal_binding = (
            (bound_account, binding_generation)
            if account_id is not None and scope != "catalog" else None
        )
        bound_identity_id = bound_account.identity_id if personal_binding else None
        broker = self.identity_broker if personal_binding else None
        owner = (
            self._rights_owner_snapshot(account_id)
            if personal_binding and broker is not None else None
        )

        def private_guard() -> None:
            if personal_binding is None:
                return
            self._ensure_personal_binding(
                *personal_binding, expected_account_id=account_id,
                expected_identity_id=bound_identity_id,
            )
            if (self.identity_broker is not broker
                    or (broker is not None
                        and self._rights_owner_snapshot(account_id) != owner)):
                raise ProviderError(
                    "Kobo-Vault : propriétaire modifié avant lecture privée"
                )

        if personal_binding is not None:
            private_guard()
        # Input validation above accepts harmless casing/outer whitespace,
        # but the Vault API must always receive the *declared* canonical
        # general-search value. Passing " ALL " to Vault would be a different
        # server-side filter and could turn a valid search into a false zero.
        options = {"field": "all", "limit": 50, "page": page}
        if account_id is None or scope == "catalog":
            options["scope"] = "catalog"
        # Core reads this stable warning channel to distinguish a public-only
        # fallback from a complete authenticated catalogue/library search.
        # Reset is done before input validation, so even rejected attempts
        # cannot leave a previous personal-denial warning visible.
        # The actual Vault client performs a public catalogue GET before a
        # private /library/search GET. Fence BETWEEN them. Third-party
        # injected catalogue adapters keep their old search() signature.
        real_catalogue = (
            personal_binding is not None
            and isinstance(self.client, KoboVaultClient)
            and isinstance(self.catalog, KoboCatalogService)
            and self.catalog.client is self.client
            and getattr(self.catalog.search, "__func__", None)
            is KoboCatalogService.search
        )
        try:
            rows = self.catalog.search(
                query, **options,
                **({"private_guard": private_guard} if real_catalogue else {}),
            )
        except KoboVaultError as exc:
            # The public Core fallback accepts only the exact search_* code
            # vocabulary. Keep the KoboVaultError *type* and verified HTTP
            # status for callers, but never propagate its HTTPX cause (which
            # may carry signed URLs/private account context). A 401/403 must
            # not be reclassified as a public service outage.
            status = exc.status_code if type(exc.status_code) is int else None
            if status == 401:
                public_code = "search_authentication_required"
            elif status == 403:
                public_code = "search_access_denied"
            elif (
                status in {408, 429}
                or type(status) is int and 500 <= status <= 599
                or type(exc.code) is str and exc.code in {
                    "network_error", "rate_limited", "server_error"
                }
            ):
                public_code = "search_temporarily_unavailable"
            else:
                public_code = None
            failure = KoboVaultError(
                "Kobo-Vault : recherche indisponible",
                code=public_code, status_code=status,
            )
            raise failure from None
        if account_id is None and scope in {"all", "digital"}:
            # #1917: the public catalogue was searched, not the personal
            # library. Report reduced coverage rather than a complete hit
            # set even when the public-only query returned zero results.
            self.last_search_warnings.append(
                "partial_results : compte Kobo non attesté ; catalogue public seulement"
            )
        # Rejected rows or branches cannot certify exhaustive search, even
        # if the valid subset contains no books. Publish fixed words only;
        # never interpolate vendor diagnostics, account IDs or product keys.
        rejections = getattr(self.catalog, "last_search_rejections", ())
        if type(rejections) in (tuple, list):
            limited = rejections[:128]
            if account_id is not None and scope != "catalog" and any(
                type(row) is dict
                and row.get("source") == "library"
                and row.get("reason") == "personal_search_denied"
                for row in limited
            ):
                self.last_search_warnings.append(
                    "partial_results : recherche personnelle refusée ; "
                    "catalogue public seulement"
                )
            if any(
                type(row) is dict
                and row.get("reason") != "personal_search_denied"
                for row in limited
            ):
                self.last_search_warnings.append(
                    "partial_results : notices Kobo invalides ignorées"
                )
        accepted: list[SearchResult] = []
        for item in rows:
            # A single broken normalized hit (including a hostile property or
            # unhashable source_collection) must not abort other safe results
            # or cause a private row to bypass the account-scope check.
            try:
                raw_metadata = getattr(item, "metadata", None)
                if raw_metadata is not None and type(raw_metadata) is not dict:
                    continue
                meta = raw_metadata if raw_metadata is not None else {}
                owned = getattr(item, "owned", None)
                collection = getattr(item, "source_collection", None)
                if (owned is not None and type(owned) is not bool):
                    continue
                if type(collection) is not str and collection is not None:
                    continue
                private = owned is True or collection in ("library", "archived")
                # "catalog" is always public even for a fully authenticated
                # session. A faulty substitute catalogue adapter cannot
                # promote private hits into an explicitly public search.
                if private and (account_id is None or scope == "catalog"):
                    continue
                if "account_id" in meta and (
                    scope == "catalog" or account_id is None
                    or type(meta["account_id"]) is not str
                    or meta["account_id"] != account_id
                ):
                    continue
            except Exception:
                # Never expose the provider's exception text or class name.
                continue
            accepted.append(item)
        cover_count = 0
        for item in accepted:
            try:
                cover = getattr(item, "thumbnail_url", None)
                if type(cover) is str and cover:
                    cover_count += 1
            except Exception:
                # Traces are advisory; a bad cover must not hide good books.
                pass
        _TRACE_LOG.debug(
            "TRACE kobo catalog plugin source=search items=%s with_cover=%s",
            len(accepted), cover_count,
        )
        if personal_binding is not None:
            # A close/rebind inside catalogue HTTP or local row projection
            # must not present stale purchased items as current search hits.
            private_guard()
        return accepted

    def _ensure_personal_binding(
        self, account: Account, generation: int, *,
        expected_account_id: str | None = None,
        expected_identity_id: Any = _UNSET_PERSONAL_IDENTITY,
    ) -> None:
        """Reject an in-flight Vault snapshot after account/session replacement."""
        if (
            self._personal_binding_generation != generation
            or not self.connected or len(self._accounts) != 1
            or self._accounts[0] is not account
            or (expected_account_id is not None
                and account.unique_id != expected_account_id)
            or (expected_identity_id is not _UNSET_PERSONAL_IDENTITY
                and account.identity_id != expected_identity_id)
            or account.provider_id != self.provider_id
            or account.credential_id != self.credential_id
            or not _strict_account_scope(account.unique_id)
        ):
            raise ProviderError("Kobo-Vault : session modifiée pendant lecture")

    def _scoped_personal_manifest(
        self, bound_account: Account, generation: int, account_id: str,
    ) -> list[dict[str, Any]]:
        """Fence every *real* Vault manifest page to one live Core owner.

        Existing injected/test Vault implementations retain their historical
        zero-argument library_manifest() contract. The real provider client
        can prevent a second private GET after a Core broker/account rebind.
        """
        vault = self.client
        if not isinstance(vault, KoboVaultClient):
            return vault.library_manifest()
        broker = self.identity_broker
        owner = self._rights_owner_snapshot(account_id) if broker is not None else None
        bound_scope = (
            bound_account.unique_id, bound_account.identity_id,
            bound_account.provider_id, bound_account.credential_id,
        )

        def check() -> None:
            self._ensure_personal_binding(
                bound_account, generation, expected_account_id=account_id,
                expected_identity_id=bound_scope[1],
            )
            if (
                (bound_account.unique_id, bound_account.identity_id,
                 bound_account.provider_id, bound_account.credential_id)
                != bound_scope
                or self.identity_broker is not broker
                or (broker is not None
                    and self._rights_owner_snapshot(account_id) != owner)
            ):
                raise ProviderError(
                    "Kobo-Vault : propriétaire modifié pendant pagination privée"
                )

        return vault.library_manifest(page_guard=check)

    def library(self) -> list[SearchResult]:
        # The rights/inventory view must be tied to a verified connection,
        # independently of the acquisition-specific backup_manifest() path.
        # A stale account from a failed reconnect is not valid authority.
        self.last_library_rejections = []
        if (
            not self.connected or len(self._accounts) != 1
            or self._accounts[0].provider_id != self.provider_id
            or self._accounts[0].credential_id != self.credential_id
            or not _strict_account_scope(self._accounts[0].unique_id)
        ):
            self.last_library_rejections.append({
                "type": "invalid_scope", "reason": "unbound_account",
            })
            return []
        bound_account = self._accounts[0]
        bound_identity_id = bound_account.identity_id
        generation = self._personal_binding_generation
        account_id = bound_account.unique_id
        items = self._scoped_personal_manifest(
            bound_account, generation, account_id,
        )
        self._ensure_personal_binding(
            bound_account, generation, expected_account_id=account_id,
            expected_identity_id=bound_identity_id,
        )
        self.last_library_rejections.extend(
            _public_kobo_rejections(
                getattr(self.client, "last_manifest_rejections", ())
            )
        )

        def foreign_account(raw: Any) -> bool:
            return (
                isinstance(raw, dict) and "account_id" in raw
                and (type(raw["account_id"]) is not str
                     or raw["account_id"] != account_id)
            )

        # A foreign row with the same product as a local row makes both
        # ambiguous, even if they arrived on different Vault pages. Never
        # leak account labels or product IDs through public diagnostics.
        foreign_products = {
            product for raw in items if foreign_account(raw)
            if (product := _strict_product_id(raw)) is not None
        }
        # The personal library can contain repeated page entries or several
        # revisions of one edition. No row wins by its order or its flags:
        # quarantining the full product prevents accidental entitlement
        # projection when Core later deduplicates or reconciles inventory.
        product_counts: dict[str, int] = {}
        tainted_products: set[str] = set()
        for raw in items:
            if type(raw) is not dict:
                continue
            product = _strict_product_id(raw)
            if product is not None:
                product_counts[product] = product_counts.get(product, 0) + 1
            else:
                # A contradictory pair of individually valid legacy aliases
                # can poison both otherwise valid sibling editions.
                for alias in ("product_id", "id"):
                    candidate = _strict_product_id({alias: raw.get(alias)})
                    if candidate is not None:
                        tainted_products.add(candidate)
        duplicated_products = tainted_products | {
            product for product, count in product_counts.items() if count > 1
        }
        _cover_count = _safe_cover_count(items, self.cover_url)
        # Never emit a credential/account, product, URL, or arbitrary Vault JSON
        # key in a trace: the list of keys itself may contain private data.
        _TRACE_LOG.debug(
            "TRACE kobo covers source=library items=%s with_cover=%s missing=%s",
            len(items), _cover_count, len(items) - _cover_count,
        )
        rows: list[SearchResult] = []
        for raw in items:
            if not isinstance(raw, dict):
                self.last_library_rejections.append({
                    "type": "invalid_shape",
                    "reason": "item_not_object",
                })
                continue
            product = _strict_product_id(raw)
            if product is None:
                self.last_library_rejections.append({
                    "type": "ProviderError", "reason": "missing_product_id",
                })
                continue
            if foreign_account(raw) or product in foreign_products:
                self.last_library_rejections.append({
                    "type": "invalid_scope",
                    "reason": ("different_account" if foreign_account(raw)
                               else "ambiguous_account_binding"),
                })
                continue
            if product in duplicated_products:
                # Keep only a fixed reason; never echo the product, account
                # identifiers, revision IDs or downstream acquisition URLs.
                self.last_library_rejections.append({
                    "type": "invalid_scope", "reason": "duplicate_product_id",
                })
                continue
            try:
                rows.append(self.normalize_item(raw, default_collection="library"))
            except Exception:
                # Normalization also touches provider-controlled scalar fields
                # and cover containers: one corrupt row must not erase the
                # remaining authenticated inventory or reveal exception text.
                self.last_library_rejections.append({
                    "type": "ProviderError", "reason": "normalization_error",
                })
        # The first fence only covers the Vault HTTP GET. A close/rebind can
        # also race normalization (including a custom catalogue adapter).
        # Never publish rights under an account generation which has changed
        # while projecting the provider's raw rows.
        self._ensure_personal_binding(
            bound_account, generation, expected_account_id=account_id,
            expected_identity_id=bound_identity_id,
        )
        return rows

    def _rights_owner_snapshot(self, expected_account: str):
        """Freeze exact Core owner candidates, including duplicates/ambiguity."""
        broker = self.identity_broker
        if broker is None:
            return None
        try:
            bindings = broker.accounts()
            if type(bindings) not in (tuple, list):
                return None
            owners = []
            for ref in bindings:
                if (getattr(ref, "provider_id", None) != "kobo"
                        or getattr(ref, "account_id", None) != expected_account):
                    continue
                # Core #3171 publishes AccountRef v2, credential-qualified.
                # A different Vault credential may legitimately have the
                # same upstream account ID; it is NOT another owner of this
                # session. Preserve legacy v1 as an ambiguous candidate when
                # v1/v2 overlap, never silently trust a credential-less row.
                version = getattr(ref, "scope_version", 1)
                if type(version) is not int or version not in (1, 2):
                    return None
                if version == 2:
                    credential = getattr(ref, "credential_id", None)
                    if type(credential) is not str or not credential:
                        return None
                    if credential != self.credential_id:
                        continue
                identity = getattr(ref, "identity_id", None)
                owners.append(identity if type(identity) is str else "")
            return (id(broker), tuple(sorted(owners)))
        except Exception:
            return None

    def rights_observation_snapshot(self, account: Account):
        """Optional #3252 account-scoped read, NEVER a backup or entitlement.

        The existing library() already guards Vault GET, duplicate product
        IDs, foreign rows and in-flight reconnects. Require the Core identity
        broker as a second owner fence; never infer owner from Account.identity_id
        supplied by an untrusted direct caller. A partial positive observation
        cannot revoke absent purchases or authorize a download.
        """
        from datetime import datetime, timezone
        from cifereca.plugin_api import (
            ProviderRightsScope, ProviderRightObservation,
            ProviderRightsSnapshot,
        )

        if (type(account) is not Account
                or type(account.identity_id) is not str
                or not account.identity_id
                or not self.connected or len(self._accounts) != 1):
            raise ProviderCapabilityError(
                "Kobo-Vault : portée des observations de droits indisponible"
            )
        bound = self._accounts[0]
        bound_identity_id = bound.identity_id
        generation = self._personal_binding_generation
        if (account.provider_id != "kobo"
                or account.credential_id != self.credential_id
                or account.unique_id != bound.unique_id
                or bound.provider_id != "kobo"
                or bound.credential_id != self.credential_id
                or not _strict_account_scope(bound.unique_id)):
            raise ProviderCapabilityError(
                "Kobo-Vault : compte des observations de droits incohérent"
            )
        # Account is mutable even when the provider's session generation is
        # fixed. A caller can be rebound while Vault is loading.
        caller_scope = (
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )
        account_id = bound.unique_id
        owner = self._rights_owner_snapshot(account_id)
        if owner is None or owner[1] != (account.identity_id,):
            raise ProviderCapabilityError(
                "Kobo-Vault : propriétaire unique non attesté pour les droits"
            )
        self._ensure_personal_binding(
            bound, generation, expected_account_id=account_id,
            expected_identity_id=bound_identity_id,
        )
        results = self.library()  # ONE existing authenticated Vault GET.
        self._ensure_personal_binding(
            bound, generation, expected_account_id=account_id,
            expected_identity_id=bound_identity_id,
        )
        if (self._rights_owner_snapshot(account_id) != owner
                or bound.unique_id != account_id
                or (account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id) != caller_scope):
            raise ProviderCapabilityError(
                "Kobo-Vault : propriétaire modifié pendant la lecture"
            )
        observed = datetime.now(timezone.utc).isoformat()
        observed_rights = []
        for row in results:
            if type(row) is not SearchResult or row.owned is not True:
                continue
            if (type(row.record_id) is not str
                    or _strict_product_id({"product_id": row.record_id})
                    != row.record_id
                    or row.source_provider != "kobo"
                    or row.source_credential != self.credential_id
                    or row.source_collection not in ("library", "archived")):
                continue
            meta = row.metadata
            if type(meta) is not dict:
                continue
            if ("account_id" in meta and
                    (type(meta["account_id"]) is not str
                     or meta["account_id"] != account_id)):
                continue
            archived = meta.get("archived")
            if archived is not None and type(archived) is not bool:
                continue
            observed_rights.append(ProviderRightObservation(
                edition_id=row.record_id, right_id=row.record_id,
                kind="purchase",
                # Archive is a library-management/download state, not a
                # purchase revocation. This row is still positively owned.
                # Delivery stays unknown and Core alone decides acquisition.
                state="active",
                evidence_kind="provider_response",
                evidence_ref="kobo.personal_manifest",
                observed_at=observed, delivery="unknown",
            ))
        self._ensure_personal_binding(
            bound, generation, expected_account_id=account_id,
            expected_identity_id=bound_identity_id,
        )
        if (self._rights_owner_snapshot(account_id) != owner
                or bound.unique_id != account_id
                or (account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id) != caller_scope):
            raise ProviderCapabilityError(
                "Kobo-Vault : propriétaire modifié pendant la lecture"
            )
        return ProviderRightsSnapshot(
            scope=ProviderRightsScope(
                caller_scope[0], "kobo", self.credential_id, account_id,
            ),
            rights=tuple(observed_rights),
            status="partial", coverage="partial",
            pagination_complete=False, branch="personal_manifest",
            observed_at=observed, snapshot_ref="kobo.personal_manifest",
        )

    def rights_delivery_offer_snapshot(
        self, account: Account, right_id: str, *, explicit_user_action: bool = False,
    ):
        """Selected, Core-admitted #3252 Vault descriptor preflight.

        A private Vault acquisition POST is not an inventory read and may
        count as a provider access. It is *never* initiated by the regular
        rights snapshot or by a missing/invalid user-action admission.
        Its payload may contain signed links; only a stable opaque offer ID
        is returned, never the payload or those URLs.
        """
        from dataclasses import replace
        from cifereca_kobo_delivery_offer import exact_kobo_offer_id

        if (explicit_user_action is not True
                or type(right_id) is not str
                or _strict_product_id({"product_id": right_id}) != right_id
                or type(account) is not Account
                or not self.connected or len(self._accounts) != 1):
            raise ProviderCapabilityError("kobo_delivery_offer_not_admitted")

        # Pin everything before the positive-right inventory read. A change
        # to the Core owner between inventory and the POST is not admissible.
        bound_accounts = self._accounts
        bound = bound_accounts[0]
        generation = self._personal_binding_generation
        client = self.client
        broker = self.identity_broker
        identity_id, account_id = account.identity_id, account.unique_id
        caller_scope = (account.identity_id, account.provider_id,
                        account.credential_id, account.unique_id)
        bound_scope = (bound.unique_id, bound.identity_id,
                       bound.provider_id, bound.credential_id)
        owner = self._rights_owner_snapshot(account_id)
        if (owner is None or owner[1] != (identity_id,)
                or type(identity_id) is not str or not identity_id
                or (account.provider_id, account.credential_id, account.unique_id)
                != ("kobo", self.credential_id, bound.unique_id)
                or (bound.provider_id, bound.credential_id)
                != ("kobo", self.credential_id)):
            raise ProviderCapabilityError("kobo_delivery_offer_owner_unverified")

        def scope_current() -> bool:
            try:
                self._ensure_personal_binding(
                    bound, generation, expected_account_id=account_id,
                    expected_identity_id=bound_scope[1],
                )
                return (
                    self.connected is True
                    and self._accounts is bound_accounts
                    and self._accounts[0] is bound
                    and self.client is client
                    and self.identity_broker is broker
                    and (bound.unique_id, bound.identity_id, bound.provider_id,
                         bound.credential_id) == bound_scope
                    and (account.identity_id, account.provider_id,
                         account.credential_id, account.unique_id) == caller_scope
                    and self._rights_owner_snapshot(account_id) == owner
                )
            except ProviderError:
                return False

        if not scope_current():
            raise ProviderCapabilityError("kobo_delivery_offer_owner_changed")
        baseline = self.rights_observation_snapshot(account)
        if not scope_current():
            raise ProviderCapabilityError("kobo_delivery_offer_owner_changed")
        right = baseline.exact(right_id, right_id, "purchase")
        if (right is None or right.state != "active"
                or right.provider_attested is not True):
            raise ProviderCapabilityError("kobo_delivery_offer_right_unobserved")
        # Exactly one POST for the exact attested product, after an explicit
        # selection. Do not fall back to delegated mode on a descriptor 403.
        payload = self.resolve_acquisition(
            right_id, mode="descriptor", include_acsm=True,
        )
        if not scope_current():
            raise ProviderCapabilityError("kobo_delivery_offer_owner_changed")
        offer_id = exact_kobo_offer_id(
            payload, identity_id=identity_id, credential_id=self.credential_id,
            account_id=account_id, product_id=right_id,
        )
        qualified = replace(
            right,
            delivery="available" if offer_id else "unknown",
            delivery_offer_id=offer_id,
        )
        # The offer classifier and dataclass projection run *after* the
        # private POST fence. A Core rebind during either local step must
        # not publish an old owner's otherwise valid offer snapshot.
        if not scope_current():
            raise ProviderCapabilityError("kobo_delivery_offer_owner_changed")
        return replace(baseline, rights=(qualified,))

    def rights_selected_delivery_offer_snapshot(
        self, account, *, edition_id, right_id, kind, branch,
        explicit_user_action=False,
    ):
        """#3252: exact Core-selected Kobo right; NO content GET or receipt.

        The Core first admits a positive SourceKey, persisted consent, session
        and epoch. This optional ABI only reuses the existing explicit Vault
        descriptor preflight; its POST cannot be triggered by inventory Sync.
        """
        from cifereca.plugin_api import ProviderRightsSnapshot

        if (explicit_user_action is not True
                or type(edition_id) is not str or not edition_id
                or type(right_id) is not str or edition_id != right_id
                or type(kind) is not str or kind != "purchase"
                or type(branch) is not str or branch != "personal_manifest"
                or type(account) is not Account
                or account.provider_id != "kobo"
                or account.credential_id != self.credential_id):
            raise ProviderCapabilityError("kobo_selected_offer_scope_mismatch")

        # An Account and even its Core owner may mutate independently while
        # the private POST and subsequent local projection execute.
        accounts = self._accounts
        if not self.connected or len(accounts) != 1:
            raise ProviderCapabilityError("kobo_selected_offer_owner_changed")
        live = accounts[0]
        generation = self._personal_binding_generation
        broker, client = self.identity_broker, self.client
        pinned = (account.identity_id, account.provider_id,
                  account.credential_id, account.unique_id)
        live_scope = (live.identity_id, live.provider_id,
                      live.credential_id, live.unique_id)
        owner = self._rights_owner_snapshot(pinned[3])
        # Kobo's live Account may not carry the Core identity yet; the
        # independent unique broker binding proves the owner. Freeze the
        # live account's *original* identity, even if empty, across the POST.
        if ((live_scope[1], live_scope[2], live_scope[3])
                != (pinned[1], pinned[2], pinned[3])
                or live_scope[0] not in (None, "", "default", pinned[0])
                or not pinned[0] or not pinned[3]
                or owner is None or owner[1] != (pinned[0],)):
            raise ProviderCapabilityError("kobo_selected_offer_owner_changed")

        offer = self.rights_delivery_offer_snapshot(
            account, right_id, explicit_user_action=True,
        )
        try:
            self._ensure_personal_binding(
                live, generation, expected_account_id=pinned[3],
                expected_identity_id=live_scope[0],
            )
        except ProviderError as exc:
            raise ProviderCapabilityError("kobo_selected_offer_owner_changed") from exc
        if (self.connected is not True or self._accounts is not accounts
                or len(self._accounts) != 1 or self._accounts[0] is not live
                or self.identity_broker is not broker or self.client is not client
                or (account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id) != pinned
                or (live.identity_id, live.provider_id,
                    live.credential_id, live.unique_id) != live_scope
                or self._rights_owner_snapshot(pinned[3]) != owner):
            raise ProviderCapabilityError("kobo_selected_offer_owner_changed")
        if (type(offer) is not ProviderRightsSnapshot
                or (offer.scope.identity_id, offer.scope.provider_id,
                    offer.scope.credential_id, offer.scope.account_id) != pinned
                or offer.branch != branch or len(offer.rights) != 1
                or offer.status != "partial" or offer.coverage != "partial"):
            raise ProviderCapabilityError("kobo_selected_offer_scope_changed")
        selected = offer.exact(edition_id, right_id, kind)
        if (selected is None or selected.state != "active"
                or not selected.provider_attested):
            raise ProviderCapabilityError("kobo_selected_offer_right_unobserved")
        return offer

    def rights_selected_delivery_candidate(
        self, account, *, edition_id, right_id, kind, branch,
        delivery_offer_id, target_dir, explicit_user_action=False,
    ):
        """Optional Core #3171 selected-candidate ABI; never a final receipt.

        Verify one currently owned exact purchase and fresh Vault descriptor,
        then stage its HTTPS payload under the Core-selected path. The public
        peer-pinned HTTP client never receives Vault Authorization or cookies.
        """
        from cifereca_kobo_selected_candidate import stage_selected_candidate
        return stage_selected_candidate(
            self, account, edition_id=edition_id, right_id=right_id,
            kind=kind, branch=branch, delivery_offer_id=delivery_offer_id,
            target_dir=target_dir,
            explicit_user_action=explicit_user_action,
        )

    def backup_manifest(self) -> list[dict[str, Any]]:
        # No diagnostic may outlive the previous manifest if fetching raises.
        self.backup_manifest_rejection_count = 0
        self.backup_manifest_policy = classify_personal_manifest((), account_id=None)
        # Backup manifests are a possible input to Core materialization, not
        # merely a public inventory view. Do not fetch or expose personal rows
        # unless the authenticated session identifies exactly one matching
        # provider, credential and account. This also invalidates stale state
        # after disconnect or failed account rebind.
        if (
            not self.connected or len(self._accounts) != 1
            or self._accounts[0].provider_id != self.provider_id
            or self._accounts[0].credential_id != self.credential_id
            or not _strict_account_scope(self._accounts[0].unique_id)
        ):
            return []
        bound_account = self._accounts[0]
        bound_identity_id = bound_account.identity_id
        generation = self._personal_binding_generation
        account_id = bound_account.unique_id
        broker = self.identity_broker
        owner = (
            self._rights_owner_snapshot(account_id)
            if broker is not None else None
        )
        items = self._scoped_personal_manifest(
            bound_account, generation, account_id,
        )
        # Unlike an empty manifest, a disconnected/replaced session is not
        # evidence that purchases disappeared. Do not publish rows OR an
        # authoritative empty manifest after this race.
        self._ensure_personal_binding(
            bound_account, generation, expected_account_id=account_id,
            expected_identity_id=bound_identity_id,
        )
        # Keep the complete raw snapshot only inside this provider call so
        # its advisory classification can count foreign-account rows. Such
        # rows must never reach Core's legacy materialization loop.
        source_items = items
        # Transfer only a bounded *count* across the public provider boundary:
        # no upstream raw items, message strings, URLs or credentials.
        rejected = getattr(self.client, "last_manifest_rejections", ())
        if type(rejected) in (list, tuple):
            # A malformed rejection entry is still proof that the fetched
            # snapshot is incomplete. Counting only dict entries previously
            # erased that evidence and could mark a partial rights manifest
            # authoritative for Core reconciliation. Never copy the payload.
            self.backup_manifest_rejection_count = min(1_000_000, len(rejected))
        else:
            # An explicit non-sequence (None, string, mapping, etc.) is an
            # unverified rejection envelope, not evidence of zero failures.
            self.backup_manifest_rejection_count = 1
        # Provider-only projection: no local file checks or entitlement
        # authorization, no further Vault request. Core can consume the
        # bounded summary after it has independently resolved the identity.
        # The authenticated account was pinned before the GET; re-reading
        # it now would trust a mutable in-place rebind after remote activity.
        if account_id is not None:
            def foreign_account(row: Any) -> bool:
                return (
                    isinstance(row, dict) and "account_id" in row
                    and (
                        type(row["account_id"]) is not str
                        or row["account_id"] != account_id
                    )
                )

            def invalid_permission_flags(row: Any) -> bool:
                # The legacy Core backup loop can acquire a manifest entry
                # unless downloadable is *exactly* False. String "false",
                # archived=0 or an explicit owned=False must NEVER silently
                # pass through as an authorized personal library item.
                return (
                    type(row) is not dict
                    or row.get("owned") is False
                    or any(
                        key in row and row[key] is not None
                        and type(row[key]) is not bool
                        for key in ("owned", "downloadable", "archived")
                    )
                )

            # Do not rely solely on Vault-client pagination hygiene. Direct
            # session callers, legacy adapters or incomplete provider snapshots
            # may still supply duplicate editions. Never hand either row to
            # the legacy Core materialization path, even when both rows look
            # individually entitled; no first/last revision may win.
            product_counts: dict[str, int] = {}
            blocked_products: set[str] = set()
            for row in items:
                if type(row) is not dict:
                    continue
                product = _strict_product_id(row)
                if product is not None:
                    product_counts[product] = product_counts.get(product, 0) + 1
                    if foreign_account(row) or invalid_permission_flags(row):
                        blocked_products.add(product)
                else:
                    # If the two legacy aliases disagree, quarantine any
                    # sibling bearing either individually valid key.
                    for alias in ("product_id", "id"):
                        candidate = _strict_product_id({alias: row.get(alias)})
                        if candidate is not None:
                            blocked_products.add(candidate)
            blocked_products.update(
                product for product, count in product_counts.items()
                if count > 1
            )
            accepted = []
            rejected_count = 0
            for item in items:
                # Legacy entries without an explicit owned/downloadable flag
                # remain visible as inventory facts, NEVER as an acquisition
                # authorization from this provider's advisory policy.
                product = _strict_product_id(item) if type(item) is dict else None
                if (
                    product is None
                    or foreign_account(item)
                    or invalid_permission_flags(item)
                    or product in blocked_products
                ):
                    rejected_count += 1
                    continue
                accepted.append(item)
            items = accepted
            # Core treats ANY provider rejection as evidence that a partial
            # manifest is unsuitable for authoritative rights prune.
            self.backup_manifest_rejection_count = min(
                1_000_000, self.backup_manifest_rejection_count + rejected_count,
            )
        # Keep positive classification private until the last session fence.
        # Another Core consumer must not observe it midway through projection.
        projected_policy = classify_personal_manifest(
            source_items,
            account_id=account_id,
            rejection_count=self.backup_manifest_rejection_count,
            acquisition_access=self.acquisition_access,
            delegation_forbidden=(
                getattr(self, "_cifereca_delegation_forbidden", False) is True
            ),
        )
        _cover_count = _safe_cover_count(items, self.cover_url)
        _TRACE_LOG.debug(
            "TRACE kobo covers source=backup items=%s with_cover=%s missing=%s",
            len(items), _cover_count, len(items) - _cover_count,
        )
        # Classification and cover extraction happen after the initial GET
        # fence; a close/reconnect there must not return a stale backup batch.
        try:
            self._ensure_personal_binding(
                bound_account, generation, expected_account_id=account_id,
                expected_identity_id=bound_identity_id,
            )
            # The per-page guard does not cover the local filtering/policy
            # projection after Vault returns. Core may reassign the exact
            # upstream account while that CPU work runs; never hand a stale
            # manifest to its legacy materialization reader.
            if (self.identity_broker is not broker
                    or (broker is not None and
                        self._rights_owner_snapshot(account_id) != owner)):
                raise ProviderError(
                    "Kobo-Vault : propriétaire modifié pendant sauvegarde"
                )
        except ProviderError:
            self.backup_manifest_rejection_count = 0
            raise
        self.backup_manifest_policy = projected_policy
        return items

    def resolve_acquisition(
        self,
        product_id: str,
        *,
        revision_id: str | None = None,
        mode: str = "descriptor",
        display_profile: str | None = None,
        include_acsm: bool | None = None,
        expected_offer_id: str | None = None,
        expected_identity_id: str | None = None,
    ) -> dict[str, Any]:
        # A direct caller of this provider method can bypass the advisory
        # backup manifest and personal search. It must not issue a private
        # acquisition request after a disconnect or a failed account rebind.
        # Entitlement and materialization remain Core decisions; this guard
        # only establishes that the provider session is bound to one account.
        if (
            not self.connected or len(self._accounts) != 1
            or self._accounts[0].provider_id != self.provider_id
            or self._accounts[0].credential_id != self.credential_id
            or not _strict_account_scope(self._accounts[0].unique_id)
        ):
            raise ProviderError("Kobo-Vault : acquisition sans compte attesté")
        # Acquisition descriptors may contain private links. A POST started
        # under one connected account must not return those links or poison
        # a different account's delegated 403 state after an in-flight rebind.
        # This is a provider-boundary fence, not a Core entitlement decision.
        bound_account = self._accounts[0]
        bound_accounts = self._accounts
        generation = self._personal_binding_generation
        account_id = bound_account.unique_id
        identity_id = bound_account.identity_id
        client = self.client
        broker = self.identity_broker
        owner = (
            self._rights_owner_snapshot(account_id)
            if broker is not None else None
        )

        def ensure_acquisition_scope() -> None:
            self._ensure_personal_binding(
                bound_account, generation,
                expected_account_id=account_id,
                expected_identity_id=identity_id,
            )
            if (self._accounts is not bound_accounts
                    or self.client is not client
                    or self.identity_broker is not broker
                    or (broker is not None
                        and self._rights_owner_snapshot(account_id) != owner)):
                raise ProviderError(
                    "Kobo-Vault : compte modifié pendant acquisition"
                )

        ensure_acquisition_scope()
        # Optional exact Core-admitted offer narrows only the selected
        # descriptor route. Never trust a string offer token as consent.
        # Brokerless direct legacy requests without this option are intact.
        if expected_offer_id is not None:
            if (type(expected_offer_id) is not str
                    or not expected_offer_id.startswith("kobo.descriptor:")
                    or len(expected_offer_id) != len("kobo.descriptor:") + 64
                    or type(expected_identity_id) is not str
                    or not expected_identity_id
                    or mode != "descriptor"
                    or broker is None or owner is None
                    or owner[1] != (expected_identity_id,)):
                raise ProviderCapabilityError("kobo_delivery_offer_not_admitted")
        # The generic engine already avoids delegated 403 retries, but direct
        # provider callers must not bypass the account-scoped refusal. A
        # delegated denial is mode-specific: it must not block a separately
        # authorized descriptor/ACSM path.
        if self.acquisition_access in _SCOPE_DENIED:
            raise ProviderError("Kobo-Vault : acquisition indisponible pour ce compte")
        if (
            type(mode) is str and mode.casefold() == "delegated"
            and getattr(self, "_cifereca_delegation_forbidden", False) is True
        ):
            raise ProviderError("Kobo-Vault : délégation indisponible pour ce compte")
        profile = display_profile if self.supports_acquisition_display_profile else None
        if self.supports_acquisition_display_profile and profile is None:
            profile = self.display_profile
        acsm = include_acsm if self.supports_acquisition_acsm else None
        try:
            result = client.acquisition(
                product_id,
                revision_id=revision_id,
                mode=mode,
                display_profile=profile,
                include_acsm=acsm,
            )
            ensure_acquisition_scope()
            if expected_offer_id is not None:
                from cifereca_kobo_delivery_offer import exact_kobo_offer_id
                current = exact_kobo_offer_id(
                    result, identity_id=expected_identity_id,
                    credential_id=self.credential_id,
                    account_id=account_id, product_id=product_id,
                )
                if current != expected_offer_id:
                    raise ProviderCapabilityError("kobo_delivery_offer_changed")
                # The descriptor is private: preserve the exact bound broker
                # fence even when local candidate projection takes time.
                ensure_acquisition_scope()
            return result
        except KoboVaultError as exc:
            # In-flight reconnect/owner change invalidates the old account's
            # error as well as its success. Never persist its 403 on B.
            ensure_acquisition_scope()
            # A direct delegated 403 must affect only this verified account,
            # just as Core's executor does. Persist the denial *before*
            # propagating so the next book cannot produce another POST.
            # Descriptor/ACSM rights remain an independent decision.
            if (
                type(mode) is str and mode.casefold() == "delegated"
                and type(exc.status_code) is int and exc.status_code == 403
            ):
                self._cifereca_delegation_forbidden = True
            raise

    def holdings(self, item: SearchResult, search_terms: str = "") -> ItemDetail:
        if item.owned:
            # The read-only holdings view must not promote an unknown Vault
            # permission into a verified local acquisition. JSON booleans are
            # the only attested archived/downloadable flags: bool("false")
            # would otherwise invent an archived state.
            raw_archived = (
                item.metadata.get("archived")
                if type(item.metadata) is dict else None
            )
            if raw_archived is not None and type(raw_archived) is not bool:
                label = "Dans votre bibliothèque Kobo · état d’archive non vérifiable"
                status = "attention_required"
            elif raw_archived is True:
                label = "Dans votre bibliothèque Kobo · archivé · restaurer sur Kobo"
                status = "attention_required"
            elif item.downloadable is False:
                label = "Dans votre bibliothèque Kobo · non téléchargeable"
                status = "owned"
            elif item.downloadable is True:
                label = "Dans votre bibliothèque Kobo · synchronisation locale disponible"
                status = "owned"
            else:
                # No entitlement to *download* has been attested. Inventory
                # membership proves only bibliographic possession, not file
                # delivery, DRM capability or local materialization.
                label = "Dans votre bibliothèque Kobo · téléchargement non vérifié"
                status = "owned"
        elif item.price:
            label = f"Disponible sur Kobo · {item.price} {item.currency or ''}".strip()
            status = "catalog"
        else:
            label = "Disponible sur Kobo"
            status = "catalog"
        return ItemDetail(
            record_id=item.record_id,
            docbase=item.docbase,
            title=item.title,
            author=item.author,
            type=item.type,
            year=item.published_at,
            thumbnail_url=item.thumbnail_url,
            availability_status=status,
            availability_label=label,
            holdings=[],
            source_provider=self.provider_id,
            source_credential=self.credential_id,
        )

    def diagnostic_state(self) -> dict[str, Any]:
        data = super().diagnostic_state()
        data.update({
            # Do not leak URL userinfo, private host/path or a signed query
            # through public diagnostics or support exports.
            "vault_url": "configured" if self.client.base_url else "missing",
            "vault_api_version": self.api_version,
            "vault_capabilities": sorted(self.capability_names),
            "acquisition_access": self.acquisition_access,
            "role": "library_provider",
            "provider_contract": f"v{self.api_version or '2.3'}",
            "vault_release": self.vault_release or None,
            "display_profile": self.display_profile if self.supports_acquisition_display_profile else None,
            "acsm_export": self.supports_acquisition_acsm,
            "manifest_materialization_policy": {
                "schema": self.backup_manifest_policy["schema"],
                "status": self.backup_manifest_policy["status"],
                "counts": dict(self.backup_manifest_policy["counts"]),
                "classified_items": self.backup_manifest_policy["classified_items"],
                "manifest_rejections": self.backup_manifest_policy["manifest_rejections"],
                "truncated": self.backup_manifest_policy["truncated"],
                "manifest_authoritative": self.backup_manifest_policy["manifest_authoritative"],
                "acquisition_authorized": False,
            },
            "catalog_search_rejections": _public_kobo_rejections(
                getattr(self.catalog, "last_search_rejections", ())
            ),
            "library_rejections": _public_kobo_rejections(
                self.last_library_rejections
            ),
        })
        return data


class KoboPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self, context):
        self._runtime_capabilities = context.broker("runtime.capabilities")
        self._identity_broker = context.broker("identities")
        if self._runtime_capabilities is None:
            raise ProviderError("Kobo : broker runtime.capabilities requis et indisponible")
        resolved = context.select_service(
            "catalog.service",
            contract="cifereca.catalog.service",
            version=">=1,<2",
        )
        self._catalog_factory = None if resolved is None else resolved.service
        if not callable(getattr(self._catalog_factory, "create", None)):
            raise ProviderError("Kobo : service catalogue requis et indisponible")
        return self

    def deactivate(self):
        self._catalog_factory = None
        self._runtime_capabilities = None
        self._identity_broker = None

    def create_session(
        self,
        *,
        credential_id: str,
        label: str,
        credentials: dict[str, Any],
        options: dict[str, Any],
        config: Any,
        storage: Any,
        diagnostics: Any,
    ) -> ProviderSession:
        return KoboSession(
            descriptor=DESCRIPTOR,
            credential_id=credential_id,
            label=label,
            credentials=credentials,
            options=options,
            catalog_factory=getattr(self, '_catalog_factory', None),
            runtime_capabilities=getattr(self, "_runtime_capabilities", None),
            identity_broker=getattr(self, "_identity_broker", None),
            config=config,
            storage=storage,
            diagnostics=diagnostics,
        )


PLUGIN = KoboPlugin()
