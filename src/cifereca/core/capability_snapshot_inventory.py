"""#2703: optional read-only bridge for already collected account evidence.

This function NEVER fetches a loan or sync source: the caller passes typed,
preselected snapshots. It adds only exact per-account observations to the
CoreUI-safe enabled source inventory and refuses ambiguous duplicates.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Any

from ..plugin_api.capability_matrix import CapabilityEvidence, CapabilityMatrix
from ..plugin_api.capability_projection import project_existing_capabilities
from ..plugin_api.loan_inventory import LoanInventorySnapshot
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from ..plugin_api.models import SyncSourceRef
from .capability_inventory import capability_matrices_for_identity


def capability_matrices_with_precollected_evidence(
    service: Any, *, identity_id: str | None = None,
    sync_sources: tuple[SyncSourceRef, ...] = (),
    loan_snapshots: tuple[LoanInventorySnapshot, ...] = (),
    rights_snapshots: tuple[ProviderRightsSnapshot, ...] = (),
) -> tuple[CapabilityMatrix, ...]:
    """Attach scoped observations without expanding a source or causing I/O.

    The caller owns current-session/epoch/freshness validation, and must have
    selected the snapshots for the exact account/identity. A stale provider
    snapshot may be historical evidence, never an admission to an action.
    Duplicate exact provider/operation/account/credential rows fail closed.
    """
    if (type(sync_sources) is not tuple or type(loan_snapshots) is not tuple
            or type(rights_snapshots) is not tuple):
        raise ValueError("capability_invalid_snapshot_collection")
    if identity_id is None:
        current = getattr(service, "current_identity", None)
        identity_id = getattr(current, "identity_id", None)
    if type(identity_id) is not str or not identity_id or len(identity_id) > 256:
        if sync_sources or loan_snapshots or rights_snapshots:
            raise ValueError("capability_invalid_identity")
        return ()

    base = capability_matrices_for_identity(service, identity_id=identity_id)
    by_provider = {matrix.provider_id: matrix for matrix in base}
    if not by_provider:
        if sync_sources or loan_snapshots or rights_snapshots:
            raise ValueError("capability_no_admitted_source")
        return ()
    # A former provider observation must not turn a currently disconnected
    # Core source into an available action. Connected and evidence status
    # remain independent dimensions of the same matrix.
    currently_connected = {
        m.provider_id: m.exact("catalog.search", "unknown").connected is True
        for m in base
    }

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
            replace(
                row,
                connected=(row.connected is True and currently_connected[source.provider_id]),
            )
            for row in projected.observations
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
            replace(
                row, enabled=True,
                connected=currently_connected[provider],
            )
            for row in projected.observations
            if (row.operation == "loan.inventory" and row.scope == "account")
            or row.operation == "loan.inventory.branch"
        )

    for snapshot in rights_snapshots:
        if type(snapshot) is not ProviderRightsSnapshot:
            raise ValueError("capability_invalid_rights_snapshot")
        if snapshot.scope.identity_id != identity_id:
            raise ValueError("capability_rights_scope_mismatch")
        provider = snapshot.scope.provider_id
        if provider not in by_provider:
            raise ValueError("capability_provider_not_admitted")
        # These observations are READ reports, NOT adjudicated entitlements.
        # Even a producer claiming complete pagination cannot authorize an
        # acquisition, prove a local file, or revoke an absent right.
        # Distinct branches stay distinct; duplicate exact account+branch
        # snapshots fail closed in CapabilityMatrix, never last-write-wins.
        observed = snapshot.status in ("partial", "success")
        extras[provider].append(CapabilityEvidence(
            provider_id=provider,
            operation="personal_inventory.rights",
            scope="account", field=snapshot.branch,
            identity_id=identity_id,
            account_id=snapshot.scope.account_id,
            credential_id=snapshot.scope.credential_id,
            status="partial" if observed else "unverified",
            implementation_state="implemented",
            source_kind="account_observation",
            # Do not echo arbitrary producer-controlled snapshot_ref, which
            # may include a private URL. Fixed contract name is sufficient
            # for this non-authorizing read-status indicator.
            evidence_ref="cifereca.rights.observation/1" if observed else "",
            reason=("rights_observation_no_action" if observed else
                    "rights_observation_" + snapshot.status),
            enabled=True, connected=currently_connected[provider],
        ))

    return tuple(
        CapabilityMatrix(
            matrix.provider_id,
            matrix.observations + tuple(extras.get(matrix.provider_id, ())),
        )
        for matrix in base
    )


__all__ = ["capability_matrices_with_precollected_evidence"]
