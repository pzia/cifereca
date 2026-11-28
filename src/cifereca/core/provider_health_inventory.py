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
        # Optional structural read ABIs: only already observed exact scopes count.
        for capability in ("detail", "rights"):
            for instance, account, owner in coordinates:
                scope = Scope(provider, instance, account, owner, capability)
                latest = health.snapshot(scope)[0]
                if latest is not None:
                    rows.append((scope, enabled, latest))
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


def diagnostic_health_observations(service, identity_id):
    """Page-only bounded metadata projection, never access provider sessions."""
    from .source_context import source_context_epoch
    from .capture_vault_service import capture_scope_authorizer
    from ..plugin_api.capture_vault import CaptureScope

    health = getattr(service, "_provider_health", None)
    if type(identity_id) is not str or not identity_id or health is None:
        return ()
    def current():
        return getattr(getattr(service, "current_identity", None),
                       "identity_id", None) == identity_id
    if not current():
        return ()
    lease = health.lease()
    if lease is None:
        return ()
    epoch = source_context_epoch(service)
    authorize = capture_scope_authorizer(service)
    rows = []
    for scope, enabled, _ in registered_health(service, health, identity_id):
        if scope.identity not in ("", identity_id):
            continue
        exact = CaptureScope(scope.provider, scope.instance, scope.account, scope.identity)
        private = bool(scope.identity) and authorize("ui.page.diagnostics", exact)
        if scope.identity and not private:
            continue
        latest, baseline, anomalies = health.snapshot(scope)
        seen = set()
        for role, observation in (("latest", latest), ("baseline", baseline),
                                  *(("anomaly", row) for row in reversed(anomalies))):
            if observation is None or observation.evidence_ref in seen:
                continue
            seen.add(observation.evidence_ref)
            owned = private and health.owns_reference(scope, observation.evidence_ref)
            rows.append({
                "provider_id": scope.provider,
                "instance_id": scope.instance,
                "account_id": scope.account,
                "identity_id": scope.identity,
                "capability": scope.capability,
                "enabled": enabled,
                "role": role,
                "outcome": observation.outcome.value,
                "version": observation.version,
                "observed_at": observation.observed_at,
                "evidence_ref": observation.evidence_ref if owned else None,
                "capture_id": observation.capture_id if owned else None,
            })
    if (not current() or source_context_epoch(service) != epoch
            or health.lease() != lease
            or getattr(service, "_provider_health", None) is not health):
        return ()
    return tuple(rows)
