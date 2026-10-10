from __future__ import annotations

from typing import Any, Iterable

from ..plugin_api.provider_collection import (
    ProviderCollection, ProviderCollectionSnapshot, VirtualProviderEntry,
    ProviderCollectionValidationError, validate_provider_collection_snapshot,
)
from .base import ProviderPlugin


class ProviderRegistry:
    """Core-owned registry. Plugins can only publish through validated collections."""

    def __init__(self, plugins: Iterable[ProviderPlugin] = ()) -> None:
        self._plugins: dict[str, ProviderPlugin] = {}
        self._collections: dict[str, ProviderCollectionSnapshot] = {}
        self._virtual_owners: dict[str, str] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: ProviderPlugin, *, replace: bool = False) -> None:
        provider_id = plugin.descriptor.id.strip().lower()
        if not provider_id:
            raise ValueError("provider id is required")
        if (
            provider_id in self._virtual_owners
            or any(provider_id == child.plugin_id
                   for snap in self._collections.values() for child in snap.children)
        ):
            raise ValueError(f"provider id reserved by a virtual collection: {provider_id}")
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

    def virtual_snapshot(self, parent_id: str) -> ProviderCollectionSnapshot | None:
        return self._collections.get(parent_id)

    def virtual_entry(self, provider_id: str) -> VirtualProviderEntry | None:
        owner = self._virtual_owners.get(provider_id)
        snapshot = self._collections.get(owner) if owner else None
        return next((c for c in snapshot.children if c.plugin_id == provider_id), None) if snapshot else None

    def virtual_parent(self, provider_id: str) -> str | None:
        return self._virtual_owners.get(provider_id)

    def virtual_ids(self, parent_id: str) -> tuple[str, ...]:
        return tuple(sorted(k for k, v in self._virtual_owners.items() if v == parent_id))

    def reconcile_collection(self, parent_id: str, service: ProviderCollection) -> tuple[str, ...]:
        """Atomically adopt one cached inventory and its locally created providers.

        Neither network discovery nor Core registry access is delegated to the
        collection. Any invalid manifest or failing factory retains the last
        known-good registry and snapshot unchanged.
        """
        if not isinstance(service, ProviderCollection):
            raise ProviderCollectionValidationError("invalid collection service")
        previous = self._collections.get(parent_id)
        owned = set(self.virtual_ids(parent_id))
        foreign = set(self._plugins) - owned
        snapshot = validate_provider_collection_snapshot(
            service.snapshot(), parent_plugin_id=parent_id,
            existing_provider_ids=foreign, previous=previous,
        )
        old_entries = {c.plugin_id: c for c in previous.children} if previous else {}
        planned = dict(self._plugins)
        for key in owned:
            planned.pop(key, None)
        new_ids: set[str] = set()
        for entry in snapshot.children:
            if entry.status != "ready":
                continue
            old_entry = old_entries.get(entry.plugin_id)
            reusable = (
                old_entry is not None
                and old_entry.status == entry.status == "ready"
                and old_entry.source_revision == entry.source_revision
                and old_entry.capabilities == entry.capabilities
                and old_entry.kind == entry.kind
            )
            reused = self._plugins.get(entry.plugin_id) if reusable else None
            plugin = reused if reused is not None else service.create_provider(entry.source_id)
            if not isinstance(plugin, ProviderPlugin):
                raise ProviderCollectionValidationError("virtual factory must return a ProviderPlugin")
            descriptor = plugin.descriptor
            if descriptor.id != entry.plugin_id or descriptor.state != "ready":
                raise ProviderCollectionValidationError("virtual factory descriptor mismatch")
            if descriptor.credential_fields:
                raise ProviderCollectionValidationError("virtual public provider must not require credentials")
            if "catalog.search" in entry.capabilities and not descriptor.capabilities.search:
                raise ProviderCollectionValidationError("virtual provider does not implement declared search")
            if entry.plugin_id in planned:
                raise ProviderCollectionValidationError("virtual provider collision")
            planned[entry.plugin_id] = plugin
            new_ids.add(entry.plugin_id)
        # Single publication point: no reader can observe half an inventory.
        self._plugins = planned
        self._collections[parent_id] = snapshot
        self._virtual_owners = {
            **{k: v for k, v in self._virtual_owners.items() if v != parent_id},
            **{k: parent_id for k in new_ids},
        }
        return tuple(sorted(new_ids))

    def retire_collection(self, parent_id: str) -> tuple[str, ...]:
        """Unpublish an inactive parent, preserving last inventory for recovery."""
        owned = self.virtual_ids(parent_id)
        if not owned:
            return ()
        self._plugins = {k: v for k, v in self._plugins.items() if k not in owned}
        self._virtual_owners = {k: v for k, v in self._virtual_owners.items() if v != parent_id}
        return owned

    def diagnostic_state(self) -> list[dict[str, Any]]:
        return [{
            "id": p.descriptor.id, "name": p.descriptor.display_name,
            "state": p.descriptor.state, "capabilities": p.descriptor.capabilities.names(),
        } for p in self.all()]
