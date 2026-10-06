from __future__ import annotations

from typing import Any, Iterable

from .base import ProviderPlugin


class ProviderRegistry:
    """Runtime registry populated exclusively by Plugin API provider services."""

    def __init__(self, plugins: Iterable[ProviderPlugin] = ()) -> None:
        self._plugins: dict[str, ProviderPlugin] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: ProviderPlugin, *, replace: bool = False) -> None:
        provider_id = plugin.descriptor.id.strip().lower()
        if not provider_id:
            raise ValueError("provider id is required")
        if provider_id in self._plugins and not replace:
            raise ValueError(f"duplicate provider id: {provider_id}")
        self._plugins[provider_id] = plugin

    def get(self, provider_id: str) -> ProviderPlugin:
        return self._plugins[provider_id.strip().lower()]

    def maybe_get(self, provider_id: str) -> ProviderPlugin | None:
        return self._plugins.get(provider_id.strip().lower())

    def all(self) -> list[ProviderPlugin]:
        return [self._plugins[k] for k in sorted(self._plugins)]

    def descriptors(self):
        return [x.descriptor for x in self.all()]

    def diagnostic_state(self) -> list[dict[str, Any]]:
        return [{
            "id": p.descriptor.id, "name": p.descriptor.display_name,
            "state": p.descriptor.state, "capabilities": p.descriptor.capabilities.names(),
        } for p in self.all()]
