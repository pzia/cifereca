"""#2703: CoreUI-facing federation of existing, already fetched evidence.

The bridge consumes immutable snapshots; it never queries the provider, runs
synchronization or derives acquisition consent from an inventory result.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.services import LibraryService
from cifereca.core.capability_snapshot_inventory import (
    capability_matrices_with_precollected_evidence,
)
from cifereca.plugin_api import LoanInventoryScope
from tests.test_core_p1_2385_catalogue_source_inventory import _rig
from tests.test_core_p1_2703_existing_capability_projection import (
    _loan_snapshot, _sync,
)


def _by_provider(matrices):
    return {row.provider_id: row for row in matrices}


def test_federated_catalogue_sync_and_loans_remain_independent_scoped_claims():
    service, calls = _rig()
    before = LibraryService.capability_matrices(service, "alice")
    matrices = LibraryService.capability_matrices_with_precollected_evidence(
        service, "alice",
        sync_sources=(_sync(account="card-a"),),
        loan_snapshots=(_loan_snapshot(),),
    )
    assert len(matrices) == len(before)
    assert {x.provider_id for x in before} == {x.provider_id for x in matrices}
    paris = _by_provider(matrices)["paris"]
    assert paris.exact("catalog.search", "authenticated",
                       identity_id="alice").status == "unverified"
    sync = paris.exact("sync.entitlements", "account",
                       identity_id="alice", account_id="card-a")
    assert sync.status == "unverified"
    assert sync.source_kind == "code"
    loan = paris.exact("loan.inventory", "account",
                       identity_id="alice", account_id="card-a",
                       credential_id="vault-a")
    assert loan.status == "supported"
    assert loan.source_kind == "account_observation"
    assert paris.exact("loan.inventory", "account", identity_id="alice",
                       account_id="card-a", credential_id="wrong") is None
    assert paris.exact("sync.entitlements", "account",
                       identity_id="bob", account_id="card-a") is None
    assert all(row.authorizes_mutation is False
               for matrix in matrices for row in matrix.observations)
    # The established catalogue inventory reads Core account bindings, but
    # this bridge must never invoke sync, provider inventory or mutation.
    assert all(call[0] in {"accounts", "enabled"} for call in calls)
    assert capability_matrices_with_precollected_evidence(
        service, identity_id="alice", sync_sources=(_sync(),),
        loan_snapshots=(_loan_snapshot(),),
    ) == matrices


def test_two_credentials_same_borrower_keep_distinct_loan_proofs():
    service, _ = _rig()
    matrices = LibraryService.capability_matrices_with_precollected_evidence(
        service, "alice",
        loan_snapshots=(
            _loan_snapshot(credential="vault-a"),
            _loan_snapshot(credential="vault-b"),
        ),
    )
    paris = _by_provider(matrices)["paris"]
    assert paris.exact("loan.inventory", "account", identity_id="alice",
                       account_id="card-a", credential_id="vault-a").status == "supported"
    assert paris.exact("loan.inventory", "account", identity_id="alice",
                       account_id="card-a", credential_id="vault-b").status == "supported"
    assert paris.exact("loan.inventory", "account", identity_id="bob",
                       account_id="card-a", credential_id="vault-b") is None
    assert paris.exact("loan.inventory.branch", "digital", field="pnblive",
                       identity_id="alice", account_id="card-a",
                       credential_id="vault-b").status == "supported"


def test_duplicate_account_evidence_fails_closed_not_last_wins():
    service, _ = _rig()
    snap = _loan_snapshot()
    with pytest.raises(ValueError, match="capability_duplicate_scope"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", loan_snapshots=(snap, snap),
        )
    source = _sync(caps=("entitlements",))
    with pytest.raises(ValueError, match="capability_duplicate_scope"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", sync_sources=(source, source),
        )


def test_cross_identity_or_unregistered_source_never_leaks_evidence():
    service, _ = _rig()
    with pytest.raises(ValueError, match="capability_sync_scope_mismatch"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", sync_sources=(_sync(identity="bob"),),
        )
    with pytest.raises(ValueError, match="capability_loan_scope_mismatch"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", loan_snapshots=(_loan_snapshot(identity="bob"),),
        )
    unknown = replace(
        _loan_snapshot(),
        scope=LoanInventoryScope("alice", "uninstalled", "vault-a", "card-a"),
    )
    with pytest.raises(ValueError, match="capability_provider_not_admitted"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", loan_snapshots=(unknown,),
        )
    with pytest.raises(ValueError, match="capability_provider_not_admitted"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "bob", loan_snapshots=(_loan_snapshot(identity="bob"),),
        )


def test_empty_unknown_identity_is_not_a_wildcard_and_does_not_fetch():
    service, calls = _rig()
    assert LibraryService.capability_matrices_with_precollected_evidence(
        service, identity_id="missing",
    ) == ()
    with pytest.raises(ValueError, match="capability_no_admitted_source"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "missing", loan_snapshots=(_loan_snapshot(),),
        )
    with pytest.raises(ValueError, match="capability_invalid_identity"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "", sync_sources=(_sync(),),
        )
    with pytest.raises(ValueError, match="capability_invalid_snapshot_collection"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", sync_sources=[_sync()],
        )
    assert not any(call[0] == "accounts" for call in calls)


def test_no_snapshots_preserves_the_existing_code_only_inventory():
    service, _ = _rig()
    assert LibraryService.capability_matrices_with_precollected_evidence(
        service, "alice",
    ) == LibraryService.capability_matrices(service, "alice")
    assert all(
        row.source_kind == "code"
        for matrix in LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice",
        ) for row in matrix.observations
    )
