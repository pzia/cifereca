"""Read installed provider registry without activation or provider I/O."""
from dataclasses import dataclass


@dataclass(frozen=True)
class InstalledHealth:
    plugin_id: str
    enabled: bool
    state: str
    observations: tuple


def installed_health(manager, health):
    # Registry lifecycle lock is released before acquiring projection lock.
    with manager.hold_descriptive_lifecycle():
        statuses = tuple(manager.statuses())
    observed = health.inventory()
    return tuple(
        InstalledHealth(status.descriptor.id, status.enabled, status.state,
                        tuple(row for row in observed if row[0].provider == status.descriptor.id))
        for status in statuses
        if status.descriptor.type in ("provider.library", "provider.collection")
    )


def registered_health(service, health, identity_id):
    """Metadata-only inventory, scoped to the current identity."""
    from .provider_health_projection import Scope
    if not identity_id or identity_id not in {row.identity_id for row in service.identities}:
        return ()
    accounts = tuple(service.accounts_for_identity(identity_id))
    rows = []
    for plugin in service.registry.all():
        descriptor = plugin.descriptor
        provider = descriptor.id
        caps = getattr(descriptor, "capabilities", None)
        enabled = service._provider_enabled_for_identity(provider, identity_id) is True
        coordinates = [(a.credential_id, a.unique_id, identity_id) for a in accounts
                       if a.provider_id == provider and a.identity_id == identity_id
                       and a.credential_id and a.unique_id and a.credential_id != "__public__"]
        if not getattr(descriptor, "credential_fields", ()):
            coordinates.append(("", "", ""))
        for capability in ("search", "personal_library", "loans"):
            if getattr(caps, capability, None) is not True:
                continue
            for instance, account, owner in coordinates:
                scope = Scope(provider, instance, account, owner, capability)
                rows.append((scope, enabled, health.snapshot(scope)[0]))
    return tuple(rows)


def cached_virtual_health(manager, storage):
    """Previously validated children of installed parents, without activation."""
    from .provider_collection_cache import ProviderCollectionSnapshotStore
    from ..plugin_api.provider_collection import ProviderCollectionValidationError
    with manager.hold_descriptive_lifecycle():
        parents = tuple(status for status in manager.statuses()
                        if status.descriptor.type == "provider.collection")
    store = ProviderCollectionSnapshotStore(storage)
    rows = []
    for parent in parents:
        try:
            snapshot = store.load(parent.descriptor.id)
        except ProviderCollectionValidationError:
            continue
        if snapshot is not None:
            rows.extend((child.plugin_id, parent.descriptor.id, parent.enabled,
                         child.capabilities, child.status) for child in snapshot.children)
    return tuple(rows)


def provider_health_count(installed, registered, cached):
    """Count unique provider IDs, not collection children twice."""
    return len({row.plugin_id for row in installed}
               | {scope.provider for scope, _, _ in registered}
               | {row[0] for row in cached})
