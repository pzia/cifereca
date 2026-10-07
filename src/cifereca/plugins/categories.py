from __future__ import annotations

from dataclasses import replace

from cifereca.plugin_api import CategoryDefinition, contract_definition

from .errors import ManifestError


CORE_CATEGORIES: tuple[CategoryDefinition, ...] = (
    CategoryDefinition("provider.library", "Fournisseur de bibliothèque", "cifereca.provider/1", "all"),
    CategoryDefinition("transport", "Transport", "cifereca.transport/1", "priority"),
    CategoryDefinition("drm.handler", "Gestion DRM", "cifereca.drm/1", "priority"),
    CategoryDefinition("runtime.adapter", "Adaptateur runtime", "cifereca.runtime/1", "priority"),
    CategoryDefinition("storage.target", "Stockage / liseuse", "cifereca.storage/1", "priority"),
    CategoryDefinition("ui.page", "Page interface", "cifereca.ui.page/1", "all"),
    CategoryDefinition("ui.titlebar", "Barre de titre", "cifereca.ui.titlebar/1", "priority"),
    CategoryDefinition("ui.widgets", "Widgets interface", "cifereca.ui.widgets/1", "priority"),
    CategoryDefinition("sync", "Synchronisation", "cifereca.sync/1", "all"),
    CategoryDefinition("document.transformer", "Transformation de document", "cifereca.document.transformer/1", "all"),
    CategoryDefinition("recommendation", "Recommandation", "cifereca.recommendation/1", "all"),
    CategoryDefinition("catalog.enrichment", "Enrichissement de catalogue", "cifereca.catalog.enrichment/1", "all"),
    CategoryDefinition("device.profile", "Profil de liseuse", "cifereca.device.profile/1", "all"),
    CategoryDefinition("artifact.transform.preset", "Preset de transformation", "cifereca.artifact.transform.preset/1", "all"),
    CategoryDefinition("maintenance.tool", "Outil de maintenance", "cifereca.maintenance.tool/1", "all"),
    CategoryDefinition("diagnostics.check", "Vérification diagnostic", "cifereca.diagnostics.check/1", "all"),
    CategoryDefinition("meta.bundle", "Bundle de plugins", "cifereca.meta/1", "all"),
)


class CategoryRegistry:
    def __init__(self) -> None:
        self._items: dict[str, CategoryDefinition] = {item.id: item for item in CORE_CATEGORIES}
        for item in CORE_CATEGORIES:
            public = contract_definition(item.id)
            if public is None:
                raise ManifestError(f"core category missing from public contract registry: {item.id}")
            if public.contract_id != item.contract_id or public.version != item.contract_version:
                raise ManifestError(f"core contract registry mismatch: {item.id}")

    def register(self, definition: CategoryDefinition) -> None:
        key = definition.id.strip()
        if not key:
            raise ManifestError("plugin category id is required")
        current = self._items.get(key)
        if current is not None:
            if current != definition:
                raise ManifestError(f"plugin category conflict: {key}")
            return
        self._items[key] = replace(definition, id=key)

    def get(self, category_id: str) -> CategoryDefinition:
        return self._items[category_id]

    def maybe_get(self, category_id: str) -> CategoryDefinition | None:
        return self._items.get(category_id)

    def all(self) -> list[CategoryDefinition]:
        return [self._items[key] for key in sorted(self._items)]
