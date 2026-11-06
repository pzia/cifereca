from __future__ import annotations

import hashlib
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from typing import Any

from cifereca_kobo_vault import KoboVaultClient, KoboVaultError
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_policy import (
    classify_personal_manifest, _strict_account_scope, _strict_product_id,
    _SCOPE_DENIED,
)
from cifereca.plugin_api import Account, ItemDetail, SearchResult, AcquisitionOffer, AccountOptionField, CredentialField, ProviderCapabilities, ProviderSearchCapabilities, ProviderCapabilityError, ProviderDescriptor, ProviderError, ProviderPlugin, ProviderSession


_TRACE_LOG = logging.getLogger("cifereca")
_UNSET_PERSONAL_IDENTITY = object()  # None is a legitimate legacy identity.

# Only protocol metadata with a known public vocabulary may reach diagnostics.
# The public /status endpoint is still an untrusted HTTP response.
_PUBLIC_VAULT_CAPABILITIES = frozenset({
    # KVault Provider API v2.3.3 (pzia/KVault/src/worker.js): public
    # /status advertises ALL capabilities, including token-restricted ones.
    "catalog_search", "library_search", "library_manifest",
    "acquisition_info", "acquisition_drm_metadata",
    "acquisition_display_profiles", "acquisition_acsm_export",
    "acquisition_library_sync", "acquisition_delegation",
    # Compatibility with older v2 provider facades.
    "library", "acquisition",
})
_PUBLIC_ACQUISITION_ACCESS = frozenset({
    "configured", "unknown", "available", "disabled", "unavailable",
    "denied", "forbidden", "requires_scope", "not_configured",
})


def _authenticated_delegate_scope(status: Any) -> bool:
    """Read KVault's EFFECTIVE token capabilities from authenticated /status.

    KVault v2.3.3 does not return token_scopes/scopes. With a bearer, its
    capabilities are filtered by hasApiScope(token, capability.scope).
    Anonymous /status advertises ALL capabilities and must never be used as
    authorization. Only connect() supplies this helper with an authenticated
    response, bound to the live client/token/generation. capability_details
    describes server features, not a user's token grants.
    """
    if type(status) is not dict:
        return False
    capabilities = status.get("capabilities")
    if (type(capabilities) is not list
            or any(type(value) is not str for value in capabilities)
            or "acquisition_delegation" not in capabilities
            or not ({"acquisition_info", "acquisition"} & set(capabilities))):
        return False
    # A scoped token alone is insufficient if no Kobo credentials are
    # available on the server to resolve the delegated recipe.
    if status.get("acquisition_access") != "configured":
        return False
    # Future explicit scope claims can only narrow, never expand, the
    # current capability grant. Ignore unfiltered capability_details.
    for field in ("token_scopes", "scopes"):
        if field in status:
            values = status[field]
            if (type(values) is not list
                    or any(type(value) is not str for value in values)
                    or "kobo.acquisition.delegate" not in values):
                return False
    return True


def _vault_token_fingerprint(client: Any) -> bytes | None:
    """Bind a positive grant to the bearer without duplicating a secret."""
    token = getattr(client, "token", None)
    return (hashlib.sha256(token.encode("utf-8")).digest()
            if type(token) is str and token else None)


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
    # Public reader-facing catalogue link belongs to this provider.
    # It must not be confused with the private Kobo-Vault API base_url.
    homepage="https://www.kobo.com",
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
    account_options=(
        AccountOptionField("base_url", "URL API Kobo-Vault"),
        # The session already consumes account.options["display_profile"].
        # The global Kobo setting is a read-only migration fallback; CoreUI
        # never needs a Kobo-specific widget or enables acquisition by saving.
        AccountOptionField(
            "display_profile", "DisplayProfile Kobo-Vault (Android/Desktop)",
            default="Android", legacy_config_key="kobo_backup.display_profile",
        ),
    ),
    max_accounts_per_identity=1,
    account_note=(
        "Connexion indirecte au compte Kobo",
        "Kobo-Vault conserve la session Kobo. cifereca utilise uniquement l'URL API et un token dédié.",
    ),
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
        # Only the explicit Core source Sync may opt into a short-lived,
        # context-local reuse of one already validated personal manifest.
        # Never an inter-operation TTL or a cross-thread cache.
        self._source_inventory_batch = ContextVar(
            'cifereca.kobo.source_inventory_batch', default=None,
        )
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
        self.supports_acquisition_delegation = False
        self._delegation_bound_client = None
        self._delegation_bound_generation = None
        self._delegation_token_fingerprint = None
        self._cifereca_delegation_forbidden = False
        self._cifereca_delegation_attempts_saved = 0

    def _delegation_runtime_ready(self) -> bool:
        """Check actual executable runtime modes, not declared names alone."""
        try:
            caps = self.runtime_capabilities.acquisition("kobo")
            return (
                caps.available is True and caps.compatible is True
                and caps.operational is True
                and type(caps.modes) in (tuple, list, set, frozenset)
                and any(mode in caps.modes
                        for mode in ("kobo_delegated", "kobo_kdrm"))
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            return False

    def _delegation_admitted(self) -> bool:
        """Never reuse an attestation after reconnect/token/runtime changes."""
        return (
            self.supports_acquisition_delegation is True
            and self.connected is True
            and self._delegation_bound_client is self.client
            and self._delegation_bound_generation == self._personal_binding_generation
            and self._delegation_token_fingerprint is not None
            and self._delegation_token_fingerprint == _vault_token_fingerprint(self.client)
            and self._delegation_runtime_ready()
        )

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
        # Pin the credential across public status, /account and the optional
        # bearer-scoped status. A token swap mid-handshake must not combine
        # another owner's account with a new bearer grant.
        connecting_client = self.client
        connecting_token_fingerprint = _vault_token_fingerprint(self.client)
        self.capability_names = set()
        self.capability_details = {}
        self.supports_acquisition_acsm = False
        self.supports_acquisition_display_profile = False
        self.supports_acquisition_delegation = False
        self._delegation_bound_client = None
        self._delegation_bound_generation = None
        self._delegation_token_fingerprint = None
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
        # KVault v2.3.3 calls the read-only offer route acquisition_info.
        # Older provider facades used the generic acquisition identifier.
        vault_acquisition = bool(
            self.capability_names & {"acquisition_info", "acquisition"}
        )
        self.supports_acquisition_acsm = (
            vault_acquisition and "adobe_adept" in runtime_modes
            and ("acquisition" in self.capability_names
                 or "acquisition_acsm_export" in self.capability_names)
        )
        self.supports_acquisition_display_profile = (
            vault_acquisition
            and bool(runtime_modes & {"kobo_delegated", "kobo_kdrm"})
            and ("acquisition" in self.capability_names
                 or "acquisition_display_profiles" in self.capability_names)
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
        # /status is public by default. A second authenticated status view
        # must contain an EXPLICIT token scope before allowing delegation.
        # A Vault without this optional introspection retains its ordinary
        # inventory, descriptor, ACSM and direct-HTTP routes, not delegation.
        authenticated_status: dict[str, Any] = {}
        scoped_read = getattr(self.client, "authenticated_capabilities", None)
        if callable(scoped_read):
            try:
                observed = scoped_read()
                if type(observed) is dict:
                    authenticated_status = observed
            except ProviderError:
                # The optional scope probe must not deny ordinary library
                # access; it fails closed for delegated requests only.
                pass
        # /status without a bearer always reports acquisition_access=unknown
        # on KVault v2.3.3. The bearer-scoped view is authoritative for the
        # currently configured credential and supports actionable diagnostics.
        access = authenticated_status.get("acquisition_access")
        if type(access) is str and access in _PUBLIC_ACQUISITION_ACCESS:
            self.acquisition_access = access
        delegation_details = self.capability_details.get("acquisition_delegation", {})
        self.supports_acquisition_delegation = (
            self.client is connecting_client
            and connecting_token_fingerprint is not None
            and connecting_token_fingerprint == _vault_token_fingerprint(self.client)
            and vault_acquisition
            and "acquisition_delegation" in self.capability_names
            and delegation_details.get("available") is not False
            and delegation_details.get("enabled") is not False
            and _authenticated_delegate_scope(authenticated_status)
            and self._delegation_runtime_ready()
        )
        if self.supports_acquisition_delegation:
            token_fingerprint = _vault_token_fingerprint(self.client)
            if token_fingerprint is None:
                self.supports_acquisition_delegation = False
            else:
                self._delegation_bound_client = self.client
                self._delegation_bound_generation = self._personal_binding_generation
                self._delegation_token_fingerprint = token_fingerprint
        # Only a new positive bearer-scope proof clears a prior delegated
        # HTTP 403. An authenticated account alone is not reauthorization.
        if self.supports_acquisition_delegation:
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
        self.supports_acquisition_delegation = False
        self._delegation_bound_client = None
        self._delegation_bound_generation = None
        self._delegation_token_fingerprint = None
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

    @contextmanager
    def source_sync_inventory(self, account: Account):
        """Reuse one validated personal-library projection within one source Sync.

        No persistent rights cache, no bypass of the live owner/binding fences.
        The context is scoped to the current execution context and discarded
        even when the Sync fails. A new explicit Sync always fetches again.
        """
        if (type(account) is not Account or not self.connected
                or len(self._accounts) != 1):
            raise ProviderCapabilityError("kobo_source_inventory_scope_invalid")
        bound = self._accounts[0]
        generation = self._personal_binding_generation
        upstream_id = bound.unique_id
        scope = (
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )
        if (scope[1:] != ("kobo", self.credential_id, upstream_id)
                or type(scope[0]) is not str or not scope[0]):
            raise ProviderCapabilityError("kobo_source_inventory_scope_invalid")
        self._ensure_personal_binding(
            bound, generation, expected_account_id=upstream_id,
            expected_identity_id=bound.identity_id,
        )
        broker = self.identity_broker
        owner = self._rights_owner_snapshot(upstream_id) if broker is not None else None
        if broker is not None and (owner is None or owner[1] != (scope[0],)):
            raise ProviderCapabilityError("kobo_source_inventory_owner_invalid")
        batch = {
            "bound": bound, "generation": generation,
            "upstream_id": upstream_id, "bound_identity": bound.identity_id,
            "caller": account, "scope": scope, "broker": broker,
            "owner": owner, "rows": None, "rejections": (),
            "pagination_complete": False,
        }
        token = self._source_inventory_batch.set(batch)
        try:
            yield
        finally:
            self._source_inventory_batch.reset(token)

    def _check_source_inventory_batch(
        self, batch: dict, bound: Account, generation: int, upstream_id: str,
    ) -> None:
        """A context cache hit has exactly the same owner as its first GET."""
        self._ensure_personal_binding(
            bound, generation, expected_account_id=upstream_id,
            expected_identity_id=batch["bound_identity"],
        )
        caller = batch["caller"]
        if (batch["bound"] is not bound
                or batch["generation"] != generation
                or batch["upstream_id"] != upstream_id
                or (caller.identity_id, caller.provider_id,
                    caller.credential_id, caller.unique_id) != batch["scope"]
                or self.identity_broker is not batch["broker"]
                or (batch["broker"] is not None and
                    self._rights_owner_snapshot(upstream_id) != batch["owner"])):
            raise ProviderCapabilityError("kobo_source_inventory_owner_changed")

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
        batch = self._source_inventory_batch.get()
        if batch is not None:
            self._check_source_inventory_batch(batch, bound_account, generation, account_id)
            if batch["rows"] is not None:
                self.last_library_rejections = list(batch["rejections"])
                return deepcopy(list(batch["rows"]))
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
        if batch is not None:
            self._check_source_inventory_batch(batch, bound_account, generation, account_id)
            batch["rows"] = tuple(deepcopy(rows))
            batch["rejections"] = tuple(self.last_library_rejections)
            # A cached personal manifest may be replayed only in this
            # source Sync. Preserve its original pagination proof together
            # with the cached data, not a mutable later client-side flag.
            batch["pagination_complete"] = (
                isinstance(self.client, KoboVaultClient)
                and self.client.last_manifest_pagination_complete is True
                and self.client.last_manifest_page_attempts > 0
            )
        return rows

    def _rights_owner_snapshot(self, expected_account: str):
        """Freeze exact Core owner candidates, including duplicates/ambiguity."""
        broker = self.identity_broker
        if broker is None:
            return None
        try:
            if len(self._accounts) != 1:
                return None
            identity_id = self._accounts[0].identity_id
            if type(identity_id) is not str or not identity_id:
                return None
            # accounts(None) follows the UI selector in the public broker.
            # This session's Core binding must determine the lookup even
            # during a global sync or a selector change between Vault pages.
            bindings = broker.accounts(identity_id)
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
            # A positive *file-capability flag* in this account's private
            # manifest merits an exact descriptor check even if the separate
            # ownership field says False. Neither owned=False nor a missing
            # right may veto a genuinely offered file (#3171, 30/10).
            # Catalogue-only rows and resources with no positive ownership
            # or downloadability claim remain outside selected acquisition.
            if (type(row) is not SearchResult
                    or (row.owned is not True and row.downloadable is not True)):
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
            # A positive provider file flag must be a JSON boolean, not
            # a coerced string/number paired with an untrusted owned value.
            # This guard does not infer any entitlement from metadata.
            if any(
                key in meta and meta[key] is not None
                and type(meta[key]) is not bool
                for key in ("owned", "downloadable")
            ):
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
                # A missing upstream 'owned' flag is not proof of a right:
                # personal-library membership identifies this exact file,
                # and its delivery can still be qualified independently.
                # Archive limits delivery, never grants or revokes ownership.
                state="active" if meta.get("owned") is True else "unknown",
                evidence_kind="provider_response",
                evidence_ref="kobo.personal_manifest",
                observed_at=observed,
                delivery="unavailable" if archived is True else "unknown",
                # This exact manifest flag is a delivery fact, not a revoked
                # purchase. Core #3662 consumes it as skipped_archived.
                delivery_unavailable_reason="archived" if archived is True else "",
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
        # Vault's official page reader records its own successful final
        # cursor. An injected/legacy manifest client has no such proof.
        # One rejected/foreign/ambiguous record prevents a false exhaustive
        # rights projection. Account/profile and generation were rechecked
        # above; a full inventory neither revokes absent rights nor grants a
        # GET of content.
        batch = self._source_inventory_batch.get()
        page_chain_complete = (
            batch.get("pagination_complete") is True
            if batch is not None and batch.get("rows") is not None
            else (
                isinstance(self.client, KoboVaultClient)
                and self.client.last_manifest_pagination_complete is True
                and self.client.last_manifest_page_attempts > 0
            )
        )
        complete = (
            page_chain_complete and not self.last_library_rejections
            and len(observed_rights) == len(results)
        )
        return ProviderRightsSnapshot(
            scope=ProviderRightsScope(
                caller_scope[0], "kobo", self.credential_id, account_id,
            ),
            rights=tuple(observed_rights),
            status="success" if complete else "partial",
            coverage="complete" if complete else "partial",
            pagination_complete=complete, branch="personal_manifest",
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
        from cifereca_kobo_delivery_offer import (
            exact_kobo_offer_id, kdrm_runtime_ready,
            kdrm_delegated_runtime_ready,
        )
        from cifereca_kobo_provider_kdrm import scoped_kdrm_source

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
        if (right is None or right.provider_attested is not True):
            raise ProviderCapabilityError("kobo_delivery_offer_right_unobserved")
        if right.delivery == "unavailable":
            # A known archive is not a rejected HTTP offer or a KDRM error.
            _TRACE_LOG.info(
                "EVENT kobo selected_offer product_id=%s outcome=unavailable "
                "reason=archived_no_acquisition", right_id,
            )
            # Keep the exact positive purchase, but do not POST a descriptor
            # for a manifest-attested archive (#514).
            return replace(
                baseline, rights=(right,), status="partial",
                coverage="partial", pagination_complete=False,
            )
        # Exactly one POST for the exact attested product, after an explicit
        # selection. Do not fall back to delegated mode on a descriptor 403.
        try:
            payload = self.resolve_acquisition(
                right_id, mode="descriptor", include_acsm=True,
            )
        except (KoboVaultError, ProviderCapabilityError, ProviderError) as error:
            # This is the ACTUAL selected descriptor failure. Status is
            # included only when the upstream exception actually supplied it.
            # The KV 2.3.3 acquisition route distinguishes archive from
            # stale revision. Keep ONLY the closed typed 409 reason, never the
            # upstream response text, token, product revision or signed link.
            # A generic conflict remains generic rather than guessing.
            status = getattr(error, "status_code", None)
            code = getattr(error, "code", None)
            error_code = (
                code if isinstance(error, KoboVaultError)
                and type(status) is int and status == 409
                and type(code) is str
                and code in {"archived_book", "revision_mismatch", "conflict"}
                else "not_applicable"
            )
            if error_code == "archived_book":
                # The authenticated, exact-account descriptor is fresher
                # evidence of *delivery unavailability* than a manifest
                # snapshot that still reports the product as unarchived.
                # Do not revoke the purchase, authorize a fallback or create
                # a content GET. A new selection can re-check after restore.
                if not scope_current():
                    raise ProviderCapabilityError(
                        "kobo_delivery_offer_owner_changed"
                    ) from None
                _TRACE_LOG.info(
                    "EVENT kobo selected_offer product_id=%s phase=descriptor "
                    "outcome=unavailable reason=archived_http_409_no_acquisition "
                    "http_status=409 error_code=archived_book", right_id,
                )
                return replace(
                    baseline, rights=(replace(
                        right, delivery="unavailable", delivery_offer_id="",
                        delivery_unavailable_reason="archived",
                    ),), status="partial", coverage="partial",
                    pagination_complete=False,
                )
            _TRACE_LOG.info(
                "EVENT kobo selected_offer product_id=%s phase=descriptor "
                "outcome=error error_type=%s http_status=%s error_code=%s",
                right_id, type(error).__name__, status, error_code,
            )
            raise
        if not scope_current():
            raise ProviderCapabilityError("kobo_delivery_offer_owner_changed")
        kdrm_ready = kdrm_runtime_ready(self)
        kdrm_legacy_ready = kdrm_delegated_runtime_ready(self)
        qualification_diagnostic = {}
        offer_id = exact_kobo_offer_id(
            payload, identity_id=identity_id, credential_id=self.credential_id,
            account_id=account_id, product_id=right_id,
            kdrm_runtime_ready=kdrm_ready,
            kdrm_delegated_ready=kdrm_legacy_ready,
            diagnostic=qualification_diagnostic,
        )
        qualified_reason = (
            "descriptor_offer_attested" if offer_id else
            "descriptor_candidate_unqualified"
        )
        delegated_resolver_called = False
        # Only the ONE descriptor candidate that reproduces the exact
        # runtime offer may be used for the delegated ContentKeys replay.
        # Vault can return an earlier, non-pinned content_access_book KDRM
        # candidate alongside the supported library_sync candidate. Taking
        # the first KDRM entry would reject the valid pinned route.
        # Conversely, a selected plain/ACSM offer must not invoke delegated
        # just because an unrelated KDRM candidate is also advertised.
        if offer_id.startswith("kobo.runtime:"):
            described = payload["acquisition"]["candidates"]
            pinned = [
                row for row in described
                if type(row) is dict and row.get("handling") == "kobo_kdrm"
                and exact_kobo_offer_id(
                    {**payload, "acquisition": {
                        **payload["acquisition"], "candidates": [row],
                    }},
                    identity_id=identity_id, credential_id=self.credential_id,
                    account_id=account_id, product_id=right_id,
                    kdrm_runtime_ready=kdrm_ready,
                    kdrm_delegated_ready=kdrm_legacy_ready,
                ) == offer_id
            ]
            if len(pinned) != 1:
                offer_id = ""
                qualified_reason = "pinned_candidate_ambiguous"
            else:
                try:
                    delegated_resolver_called = True
                    delegated = self.resolve_acquisition(
                        right_id, mode="delegated", include_acsm=False,
                    )
                    if not scope_current():
                        raise ProviderCapabilityError("kobo_delivery_offer_owner_changed")
                    source = scoped_kdrm_source(
                        delegated, pinned[0], product_id=right_id,
                        account_id=account_id, identity_id=identity_id,
                        credential_id=self.credential_id,
                        kdrm_delegated_ready=kdrm_legacy_ready,
                    )
                    if source is None:
                        offer_id = ""
                        qualified_reason = "delegated_recipe_unusable"
                    else:
                        qualified_reason = "kdrm_recipe_attested"
                except ProviderCapabilityError as error:
                    if not scope_current():
                        raise ProviderCapabilityError("kobo_delivery_offer_owner_changed") from None
                    offer_id = ""
                    qualified_reason = (
                        "delegation_scope_unattested"
                        if str(error) == "missing_delegation_scope"
                        else "delegation_capability_refused"
                    )
                except KoboVaultError as error:
                    if not scope_current():
                        raise ProviderCapabilityError("kobo_delivery_offer_owner_changed") from None
                    offer_id = ""
                    qualified_reason = (
                        "delegated_http_403" if error.status_code == 403
                        else "delegated_upstream_failed"
                    )
                except ProviderError:
                    if not scope_current():
                        raise ProviderCapabilityError("kobo_delivery_offer_owner_changed") from None
                    offer_id = ""
                    qualified_reason = (
                        "delegation_quarantined_after_previous_403"
                        if getattr(self, "_cifereca_delegation_forbidden", False)
                        else "delegated_provider_failed"
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
        # Only record observed selection facts. A missing executable offer
        # is not proof of an HTTP 403 or of a failed Kobo-Adept download.
        acquisition = payload.get("acquisition") if type(payload) is dict else None
        candidates = acquisition.get("candidates") if type(acquisition) is dict else None
        count = len(candidates) if type(candidates) is list else 0
        kdrm_present = (
            any(type(row) is dict and row.get("handling") == "kobo_kdrm"
                for row in candidates) if type(candidates) is list else False
        )
        # Rejection attributes come from the SAME admission pass. The closed
        # value categories never include signed links or delegated payloads.
        detail = ""
        if qualified_reason == "descriptor_candidate_unqualified":
            detail = (
                " qualification_reason=%s eligible_candidate_count=%s "
                "candidate_source=%s candidate_media_format=%s "
                "candidate_source_format=%s"
            ) % (
                qualification_diagnostic.get("qualification_reason", "unknown"),
                qualification_diagnostic.get("eligible_candidate_count", "unknown"),
                qualification_diagnostic.get("candidate_source", "not_applicable"),
                qualification_diagnostic.get("candidate_media_format", "not_applicable"),
                qualification_diagnostic.get("candidate_source_format", "not_applicable"),
            )
            if "candidate_rejection_counts" in qualification_diagnostic:
                counts = qualification_diagnostic["candidate_rejection_counts"]
                summary = ",".join(
                    f"{reason}:{count}" for reason, count in sorted(counts.items())
                )
                detail += " candidate_selection=%s candidate_rejection_counts=%s" % (
                    qualification_diagnostic["candidate_selection"], summary or "none",
                )
            if "candidate_route_comparison" in qualification_diagnostic:
                detail += " candidate_route_comparison=%s" % (
                    qualification_diagnostic["candidate_route_comparison"],
                )
            if "candidate_shape_counts" in qualification_diagnostic:
                shapes = qualification_diagnostic["candidate_shape_counts"]
                # Values are only short, locally whitelisted categories from
                # the exact same candidate admission pass; never raw URLs.
                summary = ";".join(
                    f"{shape}={count}" for shape, count in sorted(shapes.items())
                )
                detail += " candidate_shape_counts=%s" % (summary or "none")
        _TRACE_LOG.info(
            "EVENT kobo selected_offer product_id=%s outcome=%s reason=%s "
            "candidate_count=%s kdrm_candidate_seen=%s runtime_ready=%s "
            "delegated_resolver_called=%s%s",
            right_id, "available" if offer_id else "unknown",
            qualified_reason, count, kdrm_present, kdrm_ready,
            delegated_resolver_called, detail,
        )
        # One exact purchase preflight is never a branch-wide manifest,
        # even when the preceding Vault inventory was fully attested.
        return replace(
            baseline, rights=(qualified,), status="partial",
            coverage="partial", pagination_complete=False,
        )

    def downloadable_resource_snapshot(self, account):
        """#3252 v2: positive-only authenticated resources, not legal rights."""
        from cifereca.plugin_api import from_legacy_rights_snapshot
        return from_legacy_rights_snapshot(self.rights_observation_snapshot(account))

    def downloadable_selected_offer_snapshot(
        self, account, *, edition_id, resource_id, kind, branch,
        explicit_user_action=False,
    ):
        """#3252 v2: exact signed offer independent of optional right state."""
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
        """#3252 v2: stage candidate bytes only; Core verifies and publishes."""
        from cifereca.plugin_api import (
            SourceReference, ProviderRightsScope, DeliveryCandidate,
        )
        # An Account is mutable: freeze the exact upstream source before
        # provider I/O. A reconnect/rebind must not relabel bytes staged for
        # an earlier identity, credential, or supplier account.
        scope = ProviderRightsScope(
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )
        path, hint = self.rights_selected_delivery_candidate(
            account, edition_id=edition_id, right_id=resource_id,
            kind=kind, branch=branch, delivery_offer_id=selected_offer_id,
            target_dir=target_dir, explicit_user_action=explicit_user_action,
        )
        if (account.identity_id, account.provider_id,
                account.credential_id, account.unique_id) != (
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id):
            # Only delete a newly staged candidate within Core's own attempt
            # directory; never touch a historical placement or other file.
            try:
                staged = Path(path)
                if (staged.parent == Path(target_dir)
                        and staged.is_file() and not staged.is_symlink()):
                    staged.unlink(missing_ok=True)
            except (OSError, TypeError, ValueError):
                pass
            raise ProviderCapabilityError("selected_delivery_scope_changed")
        return DeliveryCandidate(
            source=SourceReference(
                scope, branch, edition_id, resource_id, kind,
            ),
            selected_offer_id=selected_offer_id,
            path=Path(path), provider_hint=hint,
        )

    def downloadable_selected_delivery_candidate_guarded(
        self, account, *, edition_id, resource_id, kind, branch,
        selected_offer_id, target_dir, explicit_user_action=False,
        admission_current, execute_runtime=None,
    ):
        """Core #3171 opt-in: exact v2 candidate with live revocable consent.

        No new entitlement is created. The Core callback is composed with
        existing borrower, broker, epoch and staging guards at every HTTPS hop.
        """
        from cifereca.plugin_api import (
            SourceReference, ProviderRightsScope, DeliveryCandidate,
        )
        if not callable(admission_current):
            raise ProviderCapabilityError("kobo_candidate_admission_changed")
        try:
            allowed = admission_current() is True
        except Exception:
            allowed = False
        if not allowed:
            raise ProviderCapabilityError("kobo_candidate_admission_changed")
        scope = ProviderRightsScope(
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )
        path, hint = self.rights_selected_delivery_candidate(
            account, edition_id=edition_id, right_id=resource_id,
            kind=kind, branch=branch, delivery_offer_id=selected_offer_id,
            target_dir=target_dir, explicit_user_action=explicit_user_action,
            admission_current=admission_current,
            runtime_execute=execute_runtime,
        )
        try:
            current = (
                (account.identity_id, account.provider_id,
                 account.credential_id, account.unique_id)
                == (scope.identity_id, scope.provider_id,
                    scope.credential_id, scope.account_id)
                and admission_current() is True
            )
        except Exception:
            current = False
        if not current:
            # Only clean a newly staged candidate within this Core-owned run.
            try:
                staged = Path(path)
                if (staged.parent == Path(target_dir)
                        and staged.is_file() and not staged.is_symlink()):
                    staged.unlink(missing_ok=True)
            except (OSError, TypeError, ValueError):
                pass
            raise ProviderCapabilityError("kobo_candidate_admission_changed")
        return DeliveryCandidate(
            source=SourceReference(
                scope, branch, edition_id, resource_id, kind,
            ),
            selected_offer_id=selected_offer_id,
            path=Path(path), provider_hint=hint,
        )

    def downloadable_selected_delivery_requires_runtime(
        self, account, *, edition_id, resource_id, kind, branch,
        selected_offer_id,
    ):
        """Local Core route selector bound to an exact, revalidated offer.

        No supplier I/O, entitlement inference or permission is issued here.
        The later selected-candidate reader independently rehashes the live
        descriptor. A KDRM candidate cannot masquerade as direct HTTPS merely
        by supplying a direct-route token.
        """
        if (type(account) is not Account
                or account.provider_id != self.provider_id
                or account.credential_id != self.credential_id
                or type(resource_id) is not str or not resource_id
                or edition_id != resource_id or kind != "purchase"
                or branch != "personal_manifest"
                or type(selected_offer_id) is not str
                or not any(
                    selected_offer_id.startswith(prefix)
                    and len(selected_offer_id) == len(prefix) + 64
                    and all(c in "0123456789abcdef"
                            for c in selected_offer_id[len(prefix):])
                    for prefix in ("kobo.descriptor:", "kobo.runtime:")
                )):
            raise ProviderCapabilityError("kobo_candidate_route_unconfirmed")
        return selected_offer_id.startswith("kobo.runtime:")

    def downloadable_selected_delivery_candidate_runtime_guarded(
        self, account, *, edition_id, resource_id, kind, branch,
        selected_offer_id, target_dir, explicit_user_action=False,
        admission_current, execute_runtime,
    ):
        """Optional Core runtime callback; no plugin-managed runtime service."""
        if not callable(execute_runtime):
            raise ProviderCapabilityError("kobo_candidate_runtime_unavailable")
        return self.downloadable_selected_delivery_candidate_guarded(
            account, edition_id=edition_id, resource_id=resource_id,
            kind=kind, branch=branch, selected_offer_id=selected_offer_id,
            target_dir=target_dir, explicit_user_action=explicit_user_action,
            admission_current=admission_current, execute_runtime=execute_runtime,
        )

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
        if (selected is None or not selected.provider_attested):
            raise ProviderCapabilityError("kobo_selected_offer_right_unobserved")
        return offer

    def rights_selected_delivery_candidate(
        self, account, *, edition_id, right_id, kind, branch,
        delivery_offer_id, target_dir, explicit_user_action=False,
        admission_current=None, runtime_execute=None,
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
            admission_current=admission_current,
            runtime_execute=runtime_execute,
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
                    or not any(
                        expected_offer_id.startswith(prefix)
                        and len(expected_offer_id) == len(prefix) + 64
                        and all(c in "0123456789abcdef"
                                for c in expected_offer_id[len(prefix):])
                        for prefix in ("kobo.descriptor:", "kobo.runtime:")
                    )
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
        if type(mode) is str and mode.casefold() == "delegated":
            if getattr(self, "_cifereca_delegation_forbidden", False) is True:
                raise ProviderError("Kobo-Vault : délégation indisponible pour ce compte")
            if not self._delegation_admitted():
                raise ProviderCapabilityError("missing_delegation_scope")
        profile = display_profile if self.supports_acquisition_display_profile else None
        if self.supports_acquisition_display_profile and profile is None:
            profile = self.display_profile
        # KVault defaults include_acsm to TRUE if omitted. Opting out is
        # always allowed, even when no Adobe runtime is installed: selected
        # KDRM replay must not trigger speculative get_download_link lookups.
        acsm = include_acsm if self.supports_acquisition_acsm else False
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
                from cifereca_kobo_delivery_offer import (
                    kdrm_runtime_ready, kdrm_delegated_runtime_ready,
                )
                current = exact_kobo_offer_id(
                    result, identity_id=expected_identity_id,
                    credential_id=self.credential_id,
                    account_id=account_id, product_id=product_id,
                    kdrm_runtime_ready=kdrm_runtime_ready(self),
                    kdrm_delegated_ready=kdrm_delegated_runtime_ready(self),
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
                # #514: attested archive is a normal, non-deliverable
                # ownership state, never an actionable download failure.
                # Remote restoration is not part of Cifereca's workflow.
                label = "Dans votre bibliothèque Kobo · archivé · téléchargement ignoré"
                status = "owned"
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

        def safe_manifest_count(name: str) -> int:
            value = getattr(self.client, name, 0)
            return value if type(value) is int and 0 <= value <= 1_000_000 else 0

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
            "delegation_available": self._delegation_admitted(),
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
            # Bounded diagnostics; no credential, URL, request parameters,
            # cursors or server cache claims. Counts are best-effort since
            # concurrent consumers can share one session.
            "manifest_request_stats": {
                "scans_started": safe_manifest_count("manifest_scans_started"),
                "scans_paginated": safe_manifest_count("manifest_scans_paginated"),
                "http_get_attempts": safe_manifest_count("manifest_http_get_attempts"),
                "last_scan_page_attempts": safe_manifest_count("last_manifest_page_attempts"),
                "last_scan_pagination_complete": (
                    getattr(self.client, "last_manifest_pagination_complete", False) is True
                ),
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
