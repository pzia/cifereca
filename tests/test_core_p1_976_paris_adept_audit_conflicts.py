"""#976: never collapse two historical ADEPT claims into a fake single owner.

The CLI-compatible audit stays strictly offline and read-only.
"""
from __future__ import annotations

import pytest

from cifereca.paris_adept_audit import build_paris_adept_audit
from tests.test_paris_adept_audit import FakeStorage, right


def acquisition(*, profile="adept-1", status="complete", metadata=None):
    return {
        "provider_id": "paris", "credential_id": "paris-a",
        "account_id": "primary", "identity_id": "alice", "item_id": "book",
        "status": status,
        "metadata": (
            {"provider_loan_id": "shared", "adept_binding": {"adept_profile_id": profile}}
            if metadata is None else metadata
        ),
    }


def test_two_profile_claims_in_exact_same_source_never_use_last_row_wins():
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[
            acquisition(profile="adept-1"),
            acquisition(profile="adept-2"),
        ],
    )
    first = build_paris_adept_audit(storage)
    exposure = first["loans"][0]["exposures"][0]
    assert exposure["recorded_adept_profile_id"] is None
    assert exposure["recorded_adept_profile_ids"] == ["adept-1", "adept-2"]
    assert first["loans"][0]["profile_disagreement"] is True
    assert first["loans"][0]["requires_attention"] is True
    assert first["summary"]["profile_disagreements"] == 1

    # Even the order in which historical ledger rows are returned must not
    # cause an apparent ADEPT origin to flip.
    storage.ledger.reverse()
    second = build_paris_adept_audit(storage)
    assert second["loans"][0]["exposures"][0]["recorded_adept_profile_id"] is None
    assert second["loans"][0]["exposures"][0]["recorded_adept_profile_ids"] == [
        "adept-1", "adept-2",
    ]


def test_repeated_identical_profile_claim_is_not_ambiguous():
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[acquisition(profile="adept-1"), acquisition(profile="adept-1")],
    )
    exposure = build_paris_adept_audit(storage)["loans"][0]["exposures"][0]
    assert exposure["recorded_adept_profile_id"] == "adept-1"
    assert exposure["recorded_adept_profile_ids"] == ["adept-1"]


def test_conflicting_ledger_statuses_are_inert_but_visible():
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[
            acquisition(profile="profile-a", status="complete"),
            acquisition(profile="profile-a", status="failed"),
        ],
    )
    loan = build_paris_adept_audit(storage)["loans"][0]
    assert loan["ledger_status_ambiguous"] is True
    assert loan["requires_attention"] is True
    exposure = loan["exposures"][0]
    assert exposure["acquisition_status"] == "ambiguous"
    assert exposure["acquisition_statuses"] == ["complete", "failed"]


@pytest.mark.parametrize("metadata", ["PRIVATE-TOKEN", ["fake"], True, 42, {}])
def test_corrupt_metadata_never_crashes_or_attests_a_profile(metadata):
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[acquisition(metadata=metadata)],
    )
    report = build_paris_adept_audit(storage)
    assert len(report["loans"]) == 1
    exposure = report["loans"][0]["exposures"][0]
    assert exposure.get("recorded_adept_profile_id") is None
    assert "PRIVATE-TOKEN" not in repr(report)


@pytest.mark.parametrize("second_identity", ["bob", ""])
def test_duplicate_connection_owners_can_never_attest_primary(second_identity):
    storage = FakeStorage(entitlements=[right("paris-a", "alice", "primary")])
    storage.provider_connections = lambda: [
        {"provider_id": "paris", "credential_id": "paris-a", "identity_id": "alice"},
        {"provider_id": "paris", "credential_id": "paris-a",
         "identity_id": second_identity},
    ]
    report = build_paris_adept_audit(storage)
    exposure = report["loans"][0]["exposures"][0]
    assert exposure["connection_consistent"] is False
    assert exposure["borrower_identity_id"] is None
    assert exposure["adept_profile_id"] is None
    assert exposure["binding_state"] == "primary_unproven"
    assert report["loans"][0]["requires_attention"] is True
