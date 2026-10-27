"""#2703: identity-scoped, read-only Core capability inventory for CoreUI.

Only provider descriptors already admitted by the existing Core catalogue-source
inventory are published. No plugin is called, no account is synthesized, no
sync/loan read is initiated and no capability becomes an action permission.
"""
from __future__ import annotations

from typing import Any

from ..plugin_api.capability_matrix import CapabilityMatrix
from ..plugin_api.capability_projection import project_existing_capabilities
from .catalogue_source_inventory import catalogue_source_capabilities


def capability_matrices_for_identity(
    service: Any, *, identity_id: str | None = None,
) -> tuple[CapabilityMatrix, ...]:
    """Project registered, *enabled* catalogue and account-source declarations.

    This is the default safe CoreUI inventory. It deliberately does not read
    SynchronizationBroker.sources(): that legacy projection may migrate old
    sync-state metadata. Nor does it read LoanInventorySnapshot from a live
    provider, or infer capability evidence from a former synchronization.
    Other snapshots must be supplied through their explicit scoped projector.
    """
    if identity_id is None:
        current = getattr(service, "current_identity", None)
        identity_id = getattr(current, "identity_id", None)
    if type(identity_id) is not str or not 0 < len(identity_id) <= 256:
        return ()
    # Reuse the existing Core admission and identity filter, including
    # disabled provider checks, without installing or probing a plugin.
    catalogue = catalogue_source_capabilities(service, identity_id=identity_id)
    if not catalogue:
        return ()
    wanted = {item.provider_id for item in catalogue}
    descriptors: dict[str, object] = {}
    for plugin in service.registry.all():
        descriptor = getattr(plugin, "descriptor", None)
        provider_id = getattr(descriptor, "id", None)
        if provider_id in wanted:
            # Duplicate descriptors are not a reason to select whichever
            # registered last and accidentally publish a wrong declaration.
            if provider_id in descriptors:
                raise ValueError("capability_ambiguous_provider")
            descriptors[provider_id] = descriptor
    if descriptors.keys() != wanted:
        raise ValueError("capability_provider_not_registered")

    return tuple(
        project_existing_capabilities(
            descriptors[item.provider_id], identity_id=identity_id,
            catalogue=item,
            enabled=True,
            connected=(
                item.catalog_search_available or item.personal_inventory_available
            ),
        )
        for item in catalogue
    )


__all__ = ["capability_matrices_for_identity"]
