"""#2703: optional read-only bridge for already collected account evidence.

This function NEVER fetches a loan or sync source: the caller passes typed,
preselected snapshots. It adds only exact per-account observations to the
CoreUI-safe enabled source inventory and refuses ambiguous duplicates.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..plugin_api.capability_matrix import CapabilityEvidence, CapabilityMatrix
from ..plugin_api.capability_projection import project_existing_capabilities
from ..plugin_api.loan_inventory import LoanInventorySnapshot
from ..plugin_api.models import SyncSourceRef
from .capability_inventory import capability_matrices_for_identity


def capability_matrices_with_precollected_evidence(
    service: Any, *, identity_id: str | None = None,
    sync_sources: tuple[SyncSourceRef, ...] = (),
    loan_snapshots: tuple[LoanInventorySnapshot, ...] = (),
) -> tuple[CapabilityMatrix, ...]:
    """Attach scoped observations without expanding a source or causing I/O.

    The caller owns current-session/epoch/freshness validation, and must have
    selected the snapshots for the exact account/identity. A stale provider
    snapshot may be historical evidence, never an admission to an action.
    Duplicate exact provider/operation/account/credential rows fail closed.
    """
    if type(sync_sources) is not tuple or type(loan_snapshots) is not tuple:
        raise ValueError("capability_invalid_snapshot_collection")
    if identity_id is None:
        current = getattr(service, "current_identity", None)
        identity_id = getattr(current, "identity_id", None)
    if type(identity_id) is not str or not identity_id or len(identity_id) > 256:
        if sync_sources or loan_snapshots:
            raise ValueError("capability_invalid_identity")
        return ()

    base = capability_matrices_for_identity(service, identity_id=identity_id)
    by_provider = {matrix.provider_id: matrix for matrix in base}
    if not by_provider:
        if sync_sources or loan_snapshots:
            raise ValueError("capability_no_admitted_source")
        return ()

    descriptors: dict[str, object] = {}
    for plugin in service.registry.all():
        descriptor = getattr(plugin, "descriptor", None)
        name = getattr(descriptor, "id", None)
        if name in by_provider:
            if name in descriptors:
                raise ValueError("capability_ambiguous_provider")
            descriptors[name] = descriptor
    if set(descriptors) != set(by_provider):
        raise ValueError("capability_provider_not_registered")

    extras: dict[str, list[CapabilityEvidence]] = defaultdict(list)
    for source in sync_sources:
        if type(source) is not SyncSourceRef:
            raise ValueError("capability_invalid_sync_source")
        if source.identity_id != identity_id:
            raise ValueError("capability_sync_scope_mismatch")
        if source.provider_id not in by_provider:
            raise ValueError("capability_provider_not_admitted")
        projected = project_existing_capabilities(
            descriptors[source.provider_id], identity_id=identity_id,
            sync_source=source,
        )
        extras[source.provider_id].extend(
            row for row in projected.observations
            if row.operation.startswith("sync.")
        )

    for snapshot in loan_snapshots:
        if type(snapshot) is not LoanInventorySnapshot:
            raise ValueError("capability_invalid_loan_snapshot")
        if snapshot.scope.identity_id != identity_id:
            raise ValueError("capability_loan_scope_mismatch")
        provider = snapshot.scope.provider_id
        if provider not in by_provider:
            raise ValueError("capability_provider_not_admitted")
        projected = project_existing_capabilities(
            descriptors[provider], identity_id=identity_id,
            loan_snapshot=snapshot,
        )
        extras[provider].extend(
            row for row in projected.observations
            if row.operation in ("loan.inventory", "loan.inventory.branch")
        )

    return tuple(
        CapabilityMatrix(
            matrix.provider_id,
            matrix.observations + tuple(extras.get(matrix.provider_id, ())),
        )
        for matrix in base
    )


__all__ = ["capability_matrices_with_precollected_evidence"]
