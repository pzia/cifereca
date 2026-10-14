from __future__ import annotations

import logging
import re
from typing import Any

from cifereca_kobo_vault import KoboVaultClient
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_policy import classify_personal_manifest, _strict_product_id
from cifereca.plugin_api import Account, ItemDetail, SearchResult, AcquisitionOffer, CredentialField, ProviderCapabilities, ProviderDescriptor, ProviderError, ProviderPlugin, ProviderSession


_TRACE_LOG = logging.getLogger("cifereca")

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
    credential_fields=(CredentialField("api_token", "Token API Kobo-Vault", secret=True),),
    notes=(
        "Kobo-Vault est consommé comme fournisseur Library indépendant.",
        "cifereca résout l'acquisition via Vault puis délègue l'exécution locale au contrat public Kobo-Adept.",
    ),
)


class KoboSession(ProviderSession):
    """Projection of Kobo-Vault API 2.3+ into Paris' Provider model."""

    def __init__(self, *, config: Any, storage: Any, diagnostics: Any, catalog_factory=None, runtime_capabilities=None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.config = config
        self.storage = storage
        self.diagnostics = diagnostics
        self.runtime_capabilities = runtime_capabilities
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
            parts = self.api_version.split(".")
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
        self.client.close()
        self.connected = False
        self.backup_manifest_policy = classify_personal_manifest((), account_id=None)

    def cover_url(self, item: dict[str, Any]) -> str | None:
        return self.catalog.cover_url(item)

    def normalize_item(self, item: dict[str, Any], *, default_collection: str = "catalog") -> SearchResult:
        return self.catalog.normalize_item(item, default_collection=default_collection)

    def search(self, query: str, scope: str = "all", page: int = 0, field: str = "all") -> list[SearchResult]:
        if scope == "physical":
            return []
        rows = self.catalog.search(query, field=field, limit=50, page=page)
        _TRACE_LOG.debug(
            "TRACE kobo catalog plugin source=search items=%s with_cover=%s",
            len(rows), sum(1 for row in rows if row.thumbnail_url),
        )
        return rows

    def library(self) -> list[SearchResult]:
        # The rights/inventory view must be tied to a verified connection,
        # independently of the acquisition-specific backup_manifest() path.
        # A stale account from a failed reconnect is not valid authority.
        self.last_library_rejections = []
        if not self.connected or len(self._accounts) != 1:
            self.last_library_rejections.append({
                "type": "invalid_scope", "reason": "unbound_account",
            })
            return []
        account_id = self._accounts[0].unique_id
        items = self.client.library_manifest()
        self.last_library_rejections.extend(
            dict(row)
            for row in getattr(self.client, "last_manifest_rejections", ())
            if isinstance(row, dict)
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
        _cover_count = sum(1 for x in items if isinstance(x, dict) and self.cover_url(x))
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
            try:
                rows.append(self.normalize_item(raw, default_collection="library"))
            except ProviderError:
                self.last_library_rejections.append({
                    "type": "ProviderError", "reason": "normalization_error",
                })
        return rows

    def backup_manifest(self) -> list[dict[str, Any]]:
        # No diagnostic may outlive the previous manifest if fetching raises.
        self.backup_manifest_rejection_count = 0
        self.backup_manifest_policy = classify_personal_manifest((), account_id=None)
        items = self.client.library_manifest()
        # Keep the complete raw snapshot only inside this provider call so
        # its advisory classification can count foreign-account rows. Such
        # rows must never reach Core's legacy materialization loop.
        source_items = items
        # Transfer only a bounded *count* across the public provider boundary:
        # no upstream raw items, message strings, URLs or credentials.
        rejected = getattr(self.client, "last_manifest_rejections", ())
        if isinstance(rejected, (list, tuple)):
            self.backup_manifest_rejection_count = min(
                1_000_000, sum(isinstance(item, dict) for item in rejected),
            )
        # Provider-only projection: no local file checks or entitlement
        # authorization, no further Vault request. Core can consume the
        # bounded summary after it has independently resolved the identity.
        account_id = (
            self._accounts[0].unique_id
            if self.connected and len(self._accounts) == 1
            else None
        )
        if account_id is not None:
            def foreign_account(row: Any) -> bool:
                return (
                    isinstance(row, dict) and "account_id" in row
                    and (
                        type(row["account_id"]) is not str
                        or row["account_id"] != account_id
                    )
                )

            # If a foreign row duplicates a local product ID, BOTH are
            # ambiguous. Dropping the foreign row alone would hide this
            # duplicate from Core's legacy per-product checkpoint guard.
            foreign_products = {
                product for row in items if foreign_account(row)
                if (product := _strict_product_id(row)) is not None
            }
            accepted = []
            rejected_count = 0
            for item in items:
                # Only an explicitly claimed account is checked here: legacy
                # personal-library rows without account_id may still be
                # reported, but never independently authorize downloading.
                product = _strict_product_id(item) if isinstance(item, dict) else None
                if foreign_account(item) or (
                    product is not None and product in foreign_products
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
        self.backup_manifest_policy = classify_personal_manifest(
            source_items,
            account_id=account_id,
            rejection_count=self.backup_manifest_rejection_count,
            acquisition_access=self.acquisition_access,
            delegation_forbidden=(
                getattr(self, "_cifereca_delegation_forbidden", False) is True
            ),
        )
        _cover_count = sum(1 for x in items if isinstance(x, dict) and self.cover_url(x))
        _TRACE_LOG.debug(
            "TRACE kobo covers source=backup items=%s with_cover=%s missing=%s",
            len(items), _cover_count, len(items) - _cover_count,
        )
        return items

    def resolve_acquisition(
        self,
        product_id: str,
        *,
        revision_id: str | None = None,
        mode: str = "descriptor",
        display_profile: str | None = None,
        include_acsm: bool | None = None,
    ) -> dict[str, Any]:
        profile = display_profile if self.supports_acquisition_display_profile else None
        if self.supports_acquisition_display_profile and profile is None:
            profile = self.display_profile
        acsm = include_acsm if self.supports_acquisition_acsm else None
        return self.client.acquisition(
            product_id,
            revision_id=revision_id,
            mode=mode,
            display_profile=profile,
            include_acsm=acsm,
        )

    def holdings(self, item: SearchResult, search_terms: str = "") -> ItemDetail:
        if item.owned:
            archived = bool(item.metadata.get("archived")) if isinstance(item.metadata, dict) else False
            if archived:
                label = "Dans votre bibliothèque Kobo · archivé · restaurer sur Kobo"
                status = "attention_required"
            elif item.downloadable is False:
                label = "Dans votre bibliothèque Kobo · non téléchargeable"
                status = "owned"
            else:
                label = "Dans votre bibliothèque Kobo · synchronisation locale disponible"
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
            "catalog_search_rejections": [
                dict(row)
                for row in getattr(self.catalog, "last_search_rejections", ())
                if isinstance(row, dict)
            ],
            "library_rejections": [
                dict(row)
                for row in self.last_library_rejections
                if isinstance(row, dict)
            ],
        })
        return data


class KoboPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self, context):
        self._runtime_capabilities = context.broker("runtime.capabilities")
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
            config=config,
            storage=storage,
            diagnostics=diagnostics,
        )


PLUGIN = KoboPlugin()
