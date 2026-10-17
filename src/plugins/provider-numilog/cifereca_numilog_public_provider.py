"""Numilog commercial catalogue: independent anonymous public provider.

The authenticated Numilog store (provider_id=numilog) remains exclusively in
cifereca_numilog. This provider never constructs that session, reads its
cookies, infers ownership, or implements artifact acquisition.
"""
from __future__ import annotations

from dataclasses import replace

from cifereca.plugin_api import (
    ProviderCapabilities, ProviderCapabilityError, ProviderDescriptor,
    ProviderError, ProviderPlugin, ProviderSearchCapabilities, ProviderSession,
)
from cifereca_numilog_public_client import fetch_public_search


DESCRIPTOR = ProviderDescriptor(
    id="numilog-catalog",
    display_name="Numilog — Catalogue public",
    description="Catalogue commercial Numilog anonyme, distinct des livres achetés.",
    state="ready",
    capabilities=ProviderCapabilities(search=True),
    search_capabilities=ProviderSearchCapabilities(
        fields=("all",),
        scopes=("all", "digital"),
        pagination="page",
        page_size=20,
        notes=(
            "Recherche anonyme publique brSimple : seule la recherche textuelle générale est attestée.",
            "Pagination GET GRID_PAGE observée sur une capture, pages suivantes non vérifiées en direct.",
            "Les résultats sont des offres commerciales ; aucun achat, droit, fichier ni disponibilité de téléchargement n'est déduit.",
        ),
    ),
    homepage="https://www.numilog.com/",
    notes=(
        "Catalogue public séparé du compte Numilog et de Mes achats.",
        "Accès HTTP direct, conditions de robots et pages suivantes restent à qualifier.",
    ),
)


class NumilogPublicSession(ProviderSession):
    def __init__(self, **kwargs):
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        if self.credentials:
            raise ProviderCapabilityError("Numilog public : credentials_forbidden")

    def connect(self):
        # A public provider has no account and no network handshake.
        self.connected = True
        self._accounts = []
        return []

    def close(self):
        self.connected = False
        self._accounts = []

    def enrich_public_detail(self, item):
        """Explicit detail request only, not part of connect/search/sync."""
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        from cifereca_numilog_public_client import fetch_public_detail
        from cifereca_numilog_public_detail import (
            validate_public_detail_item, merge_public_detail,
        )
        url = validate_public_detail_item(item)  # No HTTP for foreign/private rows.
        return merge_public_detail(item, fetch_public_detail(url))

    def search(self, query, scope="all", page=0, field="all"):
        if type(field) is not str or field != "all":
            raise ProviderCapabilityError("Numilog public : unsupported_field")
        if type(scope) is not str or scope not in {"all", "digital"}:
            raise ProviderCapabilityError("Numilog public : unsupported_scope")
        if not self.connected:
            raise ProviderError("Numilog public : catalogue non connecté")
        rows = fetch_public_search(query, page=page, field=field).items
        # Different provider identity is essential: the private provider may
        # acquire owned books, this singleton must never be selected for it.
        return [
            replace(row, source_provider=self.provider_id, source_credential="__public__",
                    source_collection="public_catalog", owned=False,
                    downloadable=False, download_url=None)
            for row in rows
        ]


class NumilogPublicPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def create_session(self, *, credential_id, label, credentials, options,
                       config, storage, diagnostics):
        return NumilogPublicSession(
            credential_id=credential_id, label=label,
            credentials=credentials, options=options,
        )


PLUGIN = NumilogPublicPlugin()
