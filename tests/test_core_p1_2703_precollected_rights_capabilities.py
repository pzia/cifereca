"""P1 #2703: provider rights are *read observations*, never action capabilities.

Tests the actual LibraryService facade with the real plugin_api versioned
rights envelope, plus the existing typed account-scoped capability projection.
No network, Core storage writes, provider acquisitions or entitlement changes.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.services import LibraryService
from cifereca.plugin_api import (
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from tests.test_core_p1_2385_catalogue_source_inventory import _rig

STAMP = "2026-10-27T21:15:00+00:00"


def _snapshot(
    *, identity="alice", provider="kobo", credential="a-kobo",
    account="owner-A", branch="personal_manifest", state="active",
    status="partial", coverage="partial", pagination=False,
    edition="book-1",
):
    rights = () if status in ("failed", "cancelled") else (
        ProviderRightObservation(
            edition_id=edition, right_id=edition, kind="purchase",
            state=state, evidence_kind="provider_response",
            evidence_ref="synthetic.provider_manifest",
            observed_at=STAMP, delivery="unknown",
        ),
    )
    return ProviderRightsSnapshot(
        scope=ProviderRightsScope(identity, provider, credential, account),
        rights=rights, status=status, coverage=(
            "unknown" if status in ("failed", "cancelled") else coverage
        ),
        pagination_complete=(
            None if status in ("failed", "cancelled") else pagination
        ),
        branch=branch,
        observed_at=STAMP if status not in ("failed", "cancelled") else None,
        snapshot_ref="synthetic.manifest",
    )


def _matrix(matrices, provider):
    return next(x for x in matrices if x.provider_id == provider)


def _read(snapshot, **kwargs):
    service, calls = _rig()
    matrices = LibraryService.capability_matrices_with_precollected_evidence(
        service, "alice", rights_snapshots=(snapshot,), **kwargs,
    )
    return matrices, calls


def test_kobo_positive_rights_observation_is_partial_non_authorizing_read():
    snapshot = _snapshot()
    matrices, calls = _read(snapshot)
    kobo = _matrix(matrices, "kobo")
    row = kobo.exact(
        "personal_inventory.rights", "account",
        field="personal_manifest",
        identity_id="alice", account_id="owner-A", credential_id="a-kobo",
    )
    assert row is not None
    assert row.status == "partial"
    assert row.reason == "rights_observation_no_action"
    assert row.source_kind == "account_observation"
    assert row.evidence_ref == "cifereca.rights.observation/1"
    assert row.connected is True and row.enabled is True
    assert row.authorizes_mutation is False
    assert kobo.exact(
        "personal_inventory.rights", "account",
        field="personal_manifest",
        identity_id="bob", account_id="owner-A", credential_id="a-kobo",
    ) is None
    assert kobo.exact(
        "personal_inventory.rights", "account",
        field="personal_manifest",
        identity_id="alice", account_id="owner-A", credential_id="other",
    ) is None
    assert all(x[0] in {"accounts", "enabled"} for x in calls)
    assert len(matrices) == len(LibraryService.capability_matrices(_rig()[0], "alice"))


def test_even_complete_observation_remains_non_authorizing():
    complete = _snapshot(status="success", coverage="complete", pagination=True)
    matrices, _ = _read(complete)
    row = _matrix(matrices, "kobo").exact(
        "personal_inventory.rights", "account", field="personal_manifest",
        identity_id="alice", account_id="owner-A", credential_id="a-kobo",
    )
    assert row.status == "partial"  # Source coverage is not proof to acquire.
    assert row.authorizes_mutation is False
    assert complete.authorizes_negative_reconciliation is False
    assert complete.rights[0].authorizes_mutation is False


def test_multiple_credentials_and_branches_are_kept_exact_without_cross_join():
    service, _ = _rig()
    first = _snapshot()
    second = _snapshot(account="owner-B", credential="vault-b", edition="book-2")
    branch = _snapshot(branch="archive", edition="book-3")
    matrices = LibraryService.capability_matrices_with_precollected_evidence(
        service, "alice", rights_snapshots=(first, second, branch),
    )
    kobo = _matrix(matrices, "kobo")
    for scope in (
        ("personal_manifest", "owner-A", "a-kobo"),
        ("personal_manifest", "owner-B", "vault-b"),
        ("archive", "owner-A", "a-kobo"),
    ):
        assert kobo.exact(
            "personal_inventory.rights", "account",
            field=scope[0], identity_id="alice",
            account_id=scope[1], credential_id=scope[2],
        ).status == "partial"
    assert kobo.exact(
        "personal_inventory.rights", "account",
        field="archive", identity_id="alice", account_id="owner-B",
        credential_id="vault-b",
    ) is None


def test_duplicate_same_rights_branch_refused_not_last_wins():
    service, _ = _rig()
    a = _snapshot(edition="book-A")
    b = _snapshot(edition="book-B")
    with pytest.raises(ValueError, match="capability_duplicate_scope"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", rights_snapshots=(a, b),
        )


def test_failed_or_cancelled_inventory_cannot_become_current_right_proof():
    service, _ = _rig()
    for outcome in ("failed", "cancelled"):
        matrices = LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", rights_snapshots=(
                _snapshot(provider="paris", credential="a-paris",
                          account="card-A", branch="loans", status=outcome),
            ),
        )
        paris = _matrix(matrices, "paris")
        row = paris.exact(
            "personal_inventory.rights", "account", field="loans",
            identity_id="alice", account_id="card-A", credential_id="a-paris",
        )
        assert row.status == "unverified"
        assert row.reason == "rights_observation_" + outcome
        assert row.connected is False
        assert row.authorizes_mutation is False


def test_wrong_identity_disabled_provider_and_invalid_shapes_fail_closed():
    service, _ = _rig()
    with pytest.raises(ValueError, match="capability_rights_scope_mismatch"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", rights_snapshots=(_snapshot(identity="bob"),),
        )
    with pytest.raises(ValueError, match="capability_provider_not_admitted"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "bob", rights_snapshots=(_snapshot(identity="bob"),),
        )  # Kobo disabled for Bob.
    with pytest.raises(ValueError, match="capability_provider_not_admitted"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", rights_snapshots=(
                _snapshot(provider="uninstalled"),
            ),
        )
    with pytest.raises(ValueError, match="capability_invalid_rights_snapshot"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", rights_snapshots=(object(),),
        )
    with pytest.raises(ValueError, match="capability_invalid_snapshot_collection"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "alice", rights_snapshots=[_snapshot()],
        )
    with pytest.raises(ValueError, match="capability_no_admitted_source"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, "not-a-reader", rights_snapshots=(_snapshot(),),
        )


def test_no_rights_snapshot_preserves_the_previous_projection():
    service, _ = _rig()
    expected = LibraryService.capability_matrices_with_precollected_evidence(
        service, "alice",
    )
    assert expected == LibraryService.capability_matrices_with_precollected_evidence(
        service, "alice", rights_snapshots=(),
    )
