"""#2703: conservative read-only projection of existing *public* Core contracts.

This takes already collected snapshots. It never calls a provider, broker,
catalogue search, loan reader, scheduler, downloader or storage. No
cross-product between search fields, scopes or accounts. No capability
observation grants a loan/acquisition mutation or interprets a file as a right.
"""
from __future__ import annotations

from .capability_matrix import (
    CapabilityEvidence, CapabilityMatrix, declared_provider_matrix,
)
from .loan_inventory import LoanInventorySnapshot
from .models import CatalogueSourceCapability, SyncSourceRef

# Explicitly documented names in SyncSourceRef.sync_capabilities.
_SYNC_OPERATIONS = frozenset({
    "remote_inventory", "entitlements", "local_materialization",
    "scheduled_refresh",
})


def project_existing_capabilities(
    descriptor: object,
    *,
    identity_id: str,
    catalogue: CatalogueSourceCapability | None = None,
    loan_snapshot: LoanInventorySnapshot | None = None,
    sync_source: SyncSourceRef | None = None,
    enabled: bool | None = None,
    connected: bool | None = None,
) -> CapabilityMatrix:
    """Compose exact scoped evidence without fetching or joining providers.

    The caller MUST obtain `catalogue` from the *already identity-scoped*
    `catalogue_source_capabilities(identity_id)` result: v1 catalogue refs
    themselves intentionally omit identity. All other snapshots contain
    explicit identity and are checked. A missing snapshot is not a successful
    probe, nor evidence that a provider is unsupported.
    """
    if type(identity_id) is not str or not identity_id or len(identity_id) > 256:
        raise ValueError("capability_invalid_identity")
    base = declared_provider_matrix(descriptor, enabled=enabled, connected=connected)
    provider = base.provider_id
    rows = list(base.observations)

    if catalogue is not None:
        if (type(catalogue) is not CatalogueSourceCapability
                or catalogue.schema_version != 1
                or catalogue.provider_id != provider
                or catalogue.identity_scope not in ("public", "authenticated")
                or type(catalogue.catalog_search_supported) is not bool
                or type(catalogue.catalog_search_available) is not bool
                or type(catalogue.personal_inventory_supported) is not bool
                or type(catalogue.personal_inventory_available) is not bool
                or catalogue.catalog_refresh != "unsupported"
                or catalogue.acquisition != "separate_explicit"):
            raise ValueError("capability_invalid_catalogue")
        scope = catalogue.identity_scope
        for operation, declared, available in (
            ("catalog.search", catalogue.catalog_search_supported, catalogue.catalog_search_available),
            ("personal_inventory.list", catalogue.personal_inventory_supported,
             catalogue.personal_inventory_available),
        ):
            if available and not declared:
                raise ValueError("capability_invalid_catalogue")
            rows.append(CapabilityEvidence(
                provider_id=provider, operation=operation, scope=scope,
                identity_id=identity_id, enabled=True, connected=available,
                status="unverified" if declared else "unsupported",
                implementation_state="implemented" if declared else "not_applicable",
                source_kind="code",
                reason=("connected_not_live_attested" if available else
                        "declared_not_connected" if declared else "not_declared"),
            ))
        rows.append(CapabilityEvidence(
            provider_id=provider, operation="catalog.refresh", scope=scope,
            identity_id=identity_id, enabled=True, status="unsupported",
            implementation_state="not_applicable", reason="no_snapshot_contract",
        ))

    if sync_source is not None:
        if (type(sync_source) is not SyncSourceRef
                or sync_source.provider_id != provider
                or sync_source.identity_id != identity_id
                or type(sync_source.source_id) is not str
                or not sync_source.source_id
                or type(sync_source.account_id) is not str
                or not sync_source.account_id
                or type(sync_source.sync_capabilities) is not tuple
                or any(type(x) is not str for x in sync_source.sync_capabilities)):
            raise ValueError("capability_sync_scope_mismatch")
        # This tuple declares operations available in the code; it is NOT a
        # sequence of verified server actions nor a consent/rights snapshot.
        for operation in sorted(set(sync_source.sync_capabilities) & _SYNC_OPERATIONS):
            rows.append(CapabilityEvidence(
                provider_id=provider, operation="sync." + operation,
                scope="account", identity_id=identity_id,
                account_id=sync_source.account_id,
                status="unverified", implementation_state="implemented",
                source_kind="code", reason="sync_operation_declaration_only",
                enabled=sync_source.enabled,
                connected=sync_source.connected,
            ))

    if loan_snapshot is not None:
        if (type(loan_snapshot) is not LoanInventorySnapshot
                or loan_snapshot.scope.provider_id != provider
                or loan_snapshot.scope.identity_id != identity_id):
            raise ValueError("capability_loan_scope_mismatch")
        scope = loan_snapshot.scope
        # Provider 'success' without observed complete branch evidence is
        # never a complete loan inventory. Reading a list is not a right.
        complete = (loan_snapshot.status == "success"
                    and loan_snapshot.observed_at is not None
                    and bool(loan_snapshot.branches)
                    and all(b.completeness == "complete" and
                            b.pagination_complete is True
                            for b in loan_snapshot.branches))
        status = ("supported" if complete else "partial"
                  if loan_snapshot.status in ("success", "partial") else "unverified")
        reference = ("loan-inventory:" + loan_snapshot.observed_at) if complete else ""
        rows.append(CapabilityEvidence(
            provider_id=provider, operation="loan.inventory", scope="account",
            identity_id=scope.identity_id, account_id=scope.account_id,
            credential_id=scope.credential_id,
            status=status, implementation_state="implemented",
            source_kind="account_observation",
            evidence_ref=reference,
            reason=("provider_claim_complete" if complete else
                    "snapshot_" + loan_snapshot.status + "_coverage_not_attested"),
        ))
        for branch in loan_snapshot.branches:
            attested = (loan_snapshot.status == "success"
                        and loan_snapshot.observed_at is not None
                        and branch.completeness == "complete"
                        and branch.pagination_complete is True)
            rows.append(CapabilityEvidence(
                provider_id=provider, operation="loan.inventory.branch",
                scope=branch.medium, field=branch.upstream,
                identity_id=scope.identity_id, account_id=scope.account_id,
                credential_id=scope.credential_id,
                status="supported" if attested else "partial",
                implementation_state="implemented",
                source_kind="account_observation",
                evidence_ref=("loan-inventory:" + loan_snapshot.observed_at
                              if attested else ""),
                reason=("provider_claim_complete" if attested else
                        "branch_" + branch.completeness + "_not_attested"),
            ))

    return CapabilityMatrix(provider, tuple(rows))


__all__ = ["project_existing_capabilities"]
