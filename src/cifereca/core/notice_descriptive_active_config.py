"""Lifecycle-owner bridge from qualified active inventory to durable selection."""
from .notice_consolidator_active_inventory import hold_active_consolidator_inventory
from .notice_descriptive_config_owner import configure_descriptive_selection


def reconcile_active_descriptive_selection(
    storage, manager, payload, *, expected_sha256, canonical_type, scope,
    selected_plugin_id, selected_version, expected_generation,
):
    """Commit selection CAS under the existing package mutation lease.

    Caller must serialize PluginManager lifecycle and supply an independently
    trusted archive digest for the configured owner. A disabled or absent
    candidate yields a tombstone; invalid inventory leaves the prior pin intact.
    This is not an execution authorization.
    """
    with hold_active_consolidator_inventory(
        manager, payload, expected_sha256=expected_sha256,
    ) as candidates:
        return configure_descriptive_selection(
            storage,
            declarations=tuple(row.declaration for row in candidates),
            text_schemas=tuple(row.schema for row in candidates),
            installed_registry=tuple(row.installed for row in candidates),
            canonical_type=canonical_type, scope=scope,
            selected_plugin_id=selected_plugin_id,
            selected_version=selected_version,
            expected_generation=expected_generation,
        )
