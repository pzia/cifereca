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
        for capability in ("detail", "rights", "pagination", "availability"):
            for instance, account, owner in coordinates:
                scope = Scope(provider, instance, account, owner, capability)
                latest = health.snapshot(scope)[0]
                if health.retained_observations(scope):
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


def _diagnostic_generation(service, health):
    from .source_context import source_context_epoch
    account_epoch = getattr(service, "_account_context_epoch", 0)
    activation = health.diagnostic_generation()
    if type(account_epoch) is not int or account_epoch < 0 or activation is None:
        return None
    return (source_context_epoch(service), account_epoch, *activation)


def diagnostic_health_observations(service, identity_id):
    """Page-only bounded metadata projection, never access provider sessions."""
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
    generation = _diagnostic_generation(service, health)
    if generation is None:
        return ()
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
            owned = private and health.has_unique_evidence(scope, observation.evidence_ref)
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
                "generation": generation if owned else None,
            })
    if (not current() or _diagnostic_generation(service, health) != generation
            or getattr(service, "_provider_health", None) is not health):
        return ()
    return tuple(rows)


def diagnostic_health_active_observations(service):
    identity_id = getattr(getattr(service, "current_identity", None), "identity_id", None)
    return diagnostic_health_observations(service, identity_id)


def diagnostic_health_selection_current(service, scope, capability, evidence_ref,
                                        capture_id, generation):
    """Revalidate a previously displayed exact private selection; no provider I/O."""
    from ..plugin_api.capture_vault import CaptureScope

    if (type(scope) is not CaptureScope
            or not all(type(value) is str and value for value in (
                scope.provider_id, scope.instance_id, scope.account_id, scope.identity_id,
                evidence_ref))
            or type(capability) is not str
            or capability not in ("search", "detail", "rights", "loans", "personal_library", "pagination", "availability")
            or (capture_id is not None and (type(capture_id) is not str or not capture_id))
            or type(generation) is not tuple or len(generation) != 4
            or not all(type(generation[i]) is int and generation[i] >= 0 for i in (0, 1, 3))
            or type(generation[2]) is not str):
        return False
    try:
        return any(
            row["generation"] == generation
            and row["provider_id"] == scope.provider_id
            and row["instance_id"] == scope.instance_id
            and row["account_id"] == scope.account_id
            and row["identity_id"] == scope.identity_id
            and row["capability"] == capability
            and row["evidence_ref"] == evidence_ref
            and row["capture_id"] == capture_id
            for row in diagnostic_health_observations(service, scope.identity_id)
        )
    except Exception:
        return False
