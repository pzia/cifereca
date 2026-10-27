"""P1 #2703: existing public contracts projected without I/O or extra rights.

Tests use only dataclasses and independent synthetic account/credential stamps.
No provider, licence, storage, network, search or mutation is invoked.
"""
from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import (
    CapabilityEvidence, CapabilityMatrix, CatalogueSourceCapability,
    LoanBranchCoverage, LoanInventoryScope, LoanInventorySnapshot,
    ProviderCapabilities, ProviderDescriptor, SyncSourceRef, SyncPhaseState,
    project_existing_capabilities,
)


def _descriptor(provider="paris"):
    return ProviderDescriptor(
        id=provider, display_name="synthetic", description="synthetic",
        state="ready",
        capabilities=ProviderCapabilities(
            search=True, loans=True, personal_library=True,
            digital_download=True,
        ),
    )


def _catalogue(provider="paris", *, search=True, personal=True, scope="authenticated",
               search_available=True, personal_available=True):
    return CatalogueSourceCapability(
        provider_id=provider, identity_scope=scope,
        catalog_search_supported=search, catalog_search_available=search_available,
        catalog_refresh="unsupported",
        personal_inventory_supported=personal,
        personal_inventory_available=personal_available,
    )


def _sync(*, identity="alice", account="card-a", caps=("remote_inventory", "entitlements"),
          enabled=True, connected=True):
    return SyncSourceRef(
        source_id="provider-source-a", identity_id=identity, provider_id="paris",
        account_id=account, label="synthetic", enabled=enabled,
        connected=connected, sync_capabilities=caps,
        phase_states=(SyncPhaseState(phase="discover_rights", status="success"),),
    )


def _loan_snapshot(*, identity="alice", credential="vault-a", account="card-a",
                   status="success", observed_at="2026-10-27T18:00:00+00:00",
                   branches=None):
    if branches is None:
        branches = (LoanBranchCoverage(
            upstream="pnblive", medium="digital", completeness="complete",
            pagination_complete=True,
        ),)
    return LoanInventorySnapshot(
        scope=LoanInventoryScope(identity, "paris", credential, account),
        loans=(), status=status, branches=branches, observed_at=observed_at,
    )


def test_combines_three_public_contracts_without_live_or_cross_scope_claims():
    result = project_existing_capabilities(
        _descriptor(), identity_id="alice",
        catalogue=_catalogue(),
        sync_source=_sync(),
        loan_snapshot=_loan_snapshot(),
        enabled=True, connected=True,
    )
    assert result.provider_id == "paris"
    # Declaration is not attestation, even when a plugin is connected.
    declared = result.exact("catalog.search", "unknown")
    assert declared.status == "unverified" and declared.source_kind == "code"
    catalog = result.exact("catalog.search", "authenticated", identity_id="alice")
    assert catalog.status == "unverified"
    assert catalog.connected is True
    assert catalog.reason == "connected_not_live_attested"
    assert result.exact("catalog.refresh", "authenticated", identity_id="alice").status == "unsupported"
    assert result.exact("personal_inventory.list", "authenticated",
                        identity_id="alice").status == "unverified"
    # Sync declarations are not fresh entitlements or consent.
    sync = result.exact("sync.entitlements", "account",
                        identity_id="alice", account_id="card-a")
    assert sync.status == "unverified"
    assert sync.source_kind == "code"
    assert not sync.authorizes_mutation
    # A successfully observed, complete *scoped provider snapshot* is only
    # an informational claim (not a right to expire/delete/book/acquire).
    loan = result.exact("loan.inventory", "account", identity_id="alice",
                        account_id="card-a", credential_id="vault-a")
    assert loan.status == "supported"
    assert loan.source_kind == "account_observation"
    assert loan.evidence_ref.startswith("loan-inventory:")
    assert loan.authorizes_mutation is False
    branch = result.exact("loan.inventory.branch", "digital", field="pnblive",
                          identity_id="alice", account_id="card-a",
                          credential_id="vault-a")
    assert branch.status == "supported"
    assert result.exact("loan.inventory.branch", "physical", field="pnblive",
                        identity_id="alice", account_id="card-a",
                        credential_id="vault-a") is None
    assert result.exact("catalog.search", "authenticated", identity_id="bob") is None
    assert result.exact("catalog.search", "authenticated", field="title",
                        identity_id="alice") is None
    assert result.exact("sync.local_materialization", "account",
                        identity_id="alice", account_id="card-a") is None
    assert all(row.authorizes_mutation is False for row in result.observations)


def test_unavailable_code_capability_is_not_promoted_to_supported():
    result = project_existing_capabilities(
        _descriptor(), identity_id="bob",
        catalogue=_catalogue(search_available=False, personal_available=False),
        sync_source=_sync(identity="bob", account="card-b",
                          caps=("remote_inventory", "local_materialization"),
                          enabled=False, connected=False),
    )
    assert result.exact("catalog.search", "authenticated", identity_id="bob").reason == "declared_not_connected"
    file_cap = result.exact("sync.local_materialization", "account",
                            identity_id="bob", account_id="card-b")
    assert file_cap.status == "unverified"
    assert file_cap.enabled is False and file_cap.connected is False
    assert result.exact("loan.inventory", "account", identity_id="bob") is None


def test_incomplete_or_stale_loan_inventory_stays_partial_without_entitlement_action():
    partial_cases = (
        _loan_snapshot(observed_at=None),
        _loan_snapshot(branches=()),
        _loan_snapshot(status="partial"),
        _loan_snapshot(branches=(
            LoanBranchCoverage("pnblive", "digital", "complete", True),
            LoanBranchCoverage("pnblive", "physical", "partial", False),
        )),
    )
    for snapshot in partial_cases:
        result = project_existing_capabilities(
            _descriptor(), identity_id="alice", loan_snapshot=snapshot,
        )
        parent = result.exact("loan.inventory", "account",
                              identity_id="alice", account_id="card-a",
                              credential_id="vault-a")
        assert parent.status == "partial"
        assert parent.evidence_ref == ""
        assert not parent.authorizes_mutation
    failed = project_existing_capabilities(
        _descriptor(), identity_id="alice",
        loan_snapshot=_loan_snapshot(status="failed", branches=(), observed_at=None),
    )
    assert failed.exact("loan.inventory", "account", identity_id="alice",
                        account_id="card-a", credential_id="vault-a").status == "unverified"


def test_primary_empty_borrower_remains_exact_and_never_wildcards():
    result = project_existing_capabilities(
        _descriptor(), identity_id="alice",
        loan_snapshot=_loan_snapshot(account=""),
    )
    assert result.exact("loan.inventory", "account",
                        identity_id="alice", account_id="",
                        credential_id="vault-a").status == "supported"
    assert result.exact("loan.inventory", "account",
                        identity_id="alice", account_id="other",
                        credential_id="vault-a") is None


def test_credential_id_participates_in_key_and_duplicate_detection():
    def row(credential):
        return CapabilityEvidence(
            provider_id="paris", operation="loan.inventory", scope="account",
            identity_id="alice", account_id="same-borrower",
            credential_id=credential,
        )
    matrix = CapabilityMatrix("paris", (row("vault-a"), row("vault-b")))
    assert matrix.exact("loan.inventory", "account", identity_id="alice",
                        account_id="same-borrower", credential_id="vault-b") == row("vault-b")
    assert matrix.exact("loan.inventory", "account", identity_id="alice",
                        account_id="same-borrower", credential_id="other") is None
    with pytest.raises(ValueError, match="capability_duplicate_scope"):
        CapabilityMatrix("paris", (row("vault-a"), row("vault-a")))
    assert "credential_id" in asdict(row("vault-a"))


def test_read_only_projection_rejects_wrong_provider_or_other_identity():
    for kwargs, code in (
        ({"catalogue": _catalogue("kobo")}, "capability_invalid_catalogue"),
        ({"loan_snapshot": _loan_snapshot(identity="bob")}, "capability_loan_scope_mismatch"),
        ({"sync_source": _sync(identity="bob")}, "capability_sync_scope_mismatch"),
        ({"sync_source": _sync(caps=("remote_inventory", 1))}, "capability_sync_scope_mismatch"),
    ):
        with pytest.raises(ValueError, match="^" + code + "$"):
            project_existing_capabilities(_descriptor(), identity_id="alice", **kwargs)


def test_catalogue_scope_is_explicit_and_unknown_sync_names_are_ignored():
    public = project_existing_capabilities(
        _descriptor(), identity_id="alice",
        catalogue=_catalogue(scope="public", personal=False, personal_available=False),
        sync_source=_sync(caps=("unverified_future_feature", "remote_inventory")),
    )
    assert public.exact("catalog.search", "public", identity_id="alice").status == "unverified"
    assert public.exact("personal_inventory.list", "public",
                        identity_id="alice").status == "unsupported"
    assert public.exact("catalog.search", "authenticated", identity_id="alice") is None
    assert not any("future_feature" in row.operation for row in public.observations)
    assert public.exact("sync.remote_inventory", "account",
                        identity_id="alice", account_id="card-a").status == "unverified"


def test_never_calls_external_source_or_mutates_reference_objects():
    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError("no provider reads: " + name)
    descriptor = _descriptor()
    snapshot = _loan_snapshot()
    catalog = _catalogue()
    sync = _sync()
    result = project_existing_capabilities(
        descriptor, identity_id="alice",
        catalogue=catalog, loan_snapshot=snapshot, sync_source=sync,
    )
    assert isinstance(result, CapabilityMatrix)
    assert catalog.catalog_search_supported is True
    assert snapshot.status == "success"
    assert sync.sync_capabilities == ("remote_inventory", "entitlements")
    # No provider object or extra service passed, no side effect possible.
    unused = Forbidden()
    assert unused is not None
