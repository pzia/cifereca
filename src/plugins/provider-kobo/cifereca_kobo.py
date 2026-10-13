from __future__ import annotations

import logging
import re
from typing import Any

from cifereca_kobo_vault import KoboVaultClient
from cifereca_kobo_catalog import KoboCatalogService
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
        # Do not discard a denied delegated scope until the new connection
        # has actually authenticated. A failed status/account request is not
        # proof that permissions changed, and a live session can still hold
        # the previous token after an unsuccessful reconnect.
        self.capability_names = set()
        self.capability_details = {}
        self.supports_acquisition_acsm = False
        self.supports_acquisition_display_profile = False
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
        # Reauthorization only takes effect after a successful authenticated
        # account request, not when connect() was merely invoked.
        self._cifereca_delegation_forbidden = False
        self._cifereca_delegation_attempts_saved = 0
        account_id = str(account.get("id") or account.get("account_id") or "kobo")
        display = str(account.get("display_name") or account.get("name") or account.get("email_hint") or "Compte Kobo")
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

    def cover_url(self, item: dict[str, Any]) -> str | None:
        return self.catalog.cover_url(item)

    def normalize_item(self, item: dict[str, Any], *, default_collection: str = "catalog") -> SearchResult:
        return self.catalog.normalize_item(item, default_collection=default_collection)

    def search(self, query: str, scope: str = "all", page: int = 0, field: str = "all") -> list[SearchResult]:
        if scope == "physical":
            return []
        rows = self.catalog.search(query, field=field, limit=50, page=page)
        _TRACE_LOG.debug(
            "TRACE kobo catalog plugin source=search credential=%s items=%s with_cover=%s",
            self.credential_id, len(rows), sum(1 for row in rows if row.thumbnail_url),
        )
        return rows

    def library(self) -> list[SearchResult]:
        items = self.client.library_manifest()
        self.last_library_rejections = [
            dict(row)
            for row in getattr(self.client, "last_manifest_rejections", ())
            if isinstance(row, dict)
        ]
        _cover_count = sum(1 for x in items if isinstance(x, dict) and self.cover_url(x))
        _sample_missing = next((sorted(x.keys()) for x in items if isinstance(x, dict) and not self.cover_url(x)), None)
        _TRACE_LOG.debug("TRACE kobo covers source=library credential=%s items=%s with_cover=%s missing=%s sample_missing_keys=%s", self.credential_id, len(items), _cover_count, len(items)-_cover_count, _sample_missing)
        rows: list[SearchResult] = []
        for raw in items:
            if not isinstance(raw, dict):
                self.last_library_rejections.append({
                    "type": "invalid_shape",
                    "reason": "item_not_object",
                })
                continue
            try:
                rows.append(self.normalize_item(raw, default_collection="library"))
            except ProviderError:
                reason = (
                    "missing_product_id"
                    if not str(raw.get("product_id") or raw.get("id") or "").strip()
                    else "normalization_error"
                )
                self.last_library_rejections.append({
                    "type": "ProviderError",
                    "reason": reason,
                })
        return rows

    def backup_manifest(self) -> list[dict[str, Any]]:
        self.backup_manifest_rejection_count = 0
        items = self.client.library_manifest()
        # Transfer only a bounded *count* across the public provider boundary:
        # no upstream raw items, message strings, URLs or credentials.
        rejected = getattr(self.client, "last_manifest_rejections", ())
        if isinstance(rejected, (list, tuple)):
            self.backup_manifest_rejection_count = min(
                1_000_000, sum(isinstance(item, dict) for item in rejected),
            )
        _cover_count = sum(1 for x in items if isinstance(x, dict) and self.cover_url(x))
        _sample_missing = next((sorted(x.keys()) for x in items if isinstance(x, dict) and not self.cover_url(x)), None)
        _TRACE_LOG.debug("TRACE kobo covers source=backup credential=%s items=%s with_cover=%s missing=%s sample_missing_keys=%s", self.credential_id, len(items), _cover_count, len(items)-_cover_count, _sample_missing)
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
