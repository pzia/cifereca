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


def test_duplicate_grants_for_exact_paris_account_are_not_last_row_wins():
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "linked")],
        grants=[
            {
                "credential_id": "paris-a", "account_id": "linked",
                "connection_identity_id": "alice", "borrower_identity_id": "alice",
                "profile_id": "profile-a", "confirmed_by": "explicit",
            },
            {
                "credential_id": "paris-a", "account_id": "linked",
                "connection_identity_id": "alice", "borrower_identity_id": "bob",
                "profile_id": "profile-b", "confirmed_by": "explicit",
            },
        ],
    )
    before = build_paris_adept_audit(storage)
    storage.grants.reverse()
    after = build_paris_adept_audit(storage)
    for report in (before, after):
        exposure = report["loans"][0]["exposures"][0]
        assert exposure["binding_state"] == "ambiguous_binding"
        assert exposure["borrower_identity_id"] is None
        assert exposure["adept_profile_id"] is None
        assert report["loans"][0]["requires_attention"] is True


def test_configured_profile_difference_is_not_an_attested_activation_conflict():
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[acquisition(profile="historical-hb")],
    )
    report = build_paris_adept_audit(storage)
    loan = report["loans"][0]
    assert loan["profile_disagreement"] is True  # deprecated aggregate
    assert loan["recorded_profile_conflict"] is False
    assert loan["configured_profile_divergence"] is False
    assert loan["configured_vs_recorded_difference"] is True
    assert loan["origin_resolution"] == "unknown"  # no ADEPT loan reference
    assert "n'atteste pas une mauvaise activation" in report["note"]


def test_one_recorded_adobe_loan_is_not_an_authorized_return():
    binding = {"adept_profile_id": "historical-hb", "adept_loan_id": "PRIVATE-ACTIVATION-01"}
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[acquisition(metadata={
            "provider_loan_id": "shared", "adept_binding": binding,
        })],
    )
    report = build_paris_adept_audit(storage)
    loan = report["loans"][0]
    exposure = loan["exposures"][0]
    assert loan["origin_resolution"] == "single_recorded"
    assert loan["adept_loan_reference_disagreement"] is False
    assert len(exposure["recorded_adept_loan_refs"]) == 1
    assert exposure["recorded_adept_loan_ref"] == exposure["recorded_adept_loan_refs"][0]
    assert "PRIVATE-ACTIVATION" not in repr(report)
    assert "autorisation de retour" in report["note"]


def test_two_adept_loan_references_on_one_source_never_collapse_to_single_origin():
    def evidence(loan_ref):
        return acquisition(metadata={
            "provider_loan_id": "shared",
            "adept_binding": {
                "adept_profile_id": "same-historical-profile",
                "adept_loan_id": loan_ref,
            },
        })
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[evidence("PRIVATE-LICENSE-1"), evidence("PRIVATE-LICENSE-2")],
    )
    report = build_paris_adept_audit(storage)
    loan = report["loans"][0]
    exposure = loan["exposures"][0]
    assert loan["origin_resolution"] == "conflicting_records"
    assert loan["adept_loan_reference_disagreement"] is True
    assert loan["recorded_profile_conflict"] is False
    assert exposure["recorded_adept_loan_ref"] is None
    assert len(exposure["recorded_adept_loan_refs"]) == 2
    assert report["summary"]["adept_loan_reference_disagreements"] == 1
    assert "PRIVATE-LICENSE" not in repr(report)
    storage.ledger.reverse()
    assert build_paris_adept_audit(storage) == report


def test_reused_paris_loan_id_with_distinct_items_is_not_one_adobe_transaction():
    first = acquisition(metadata={
        "provider_loan_id": "shared",
        "adept_binding": {"adept_profile_id": "profile-a", "adept_loan_id": "PRIVATE-1"},
    })
    other = acquisition(metadata={
        "provider_loan_id": "shared",
        "adept_binding": {"adept_profile_id": "profile-a", "adept_loan_id": "PRIVATE-2"},
    })
    other["item_id"] = "different-item"
    storage = FakeStorage(entitlements=[
        right("paris-a", "alice", "primary", item="book"),
        right("paris-a", "alice", "primary", item="different-item"),
    ], acquisitions=[first, other])
    report = build_paris_adept_audit(storage)
    loan = report["loans"][0]
    assert loan["multiple_item_ids"] is True
    assert loan["origin_resolution"] == "unknown"
    # These are two distinct items: distinct Adobe licenses do not conflict
    # as one transaction merely because Paris reused its loan identifier.
    assert loan["adept_loan_reference_disagreement"] is False
    assert "PRIVATE-" not in repr(report)


def test_failed_adept_attempt_does_not_overrule_successful_recorded_origin():
    completed = acquisition(profile="historical-hb", status="complete", metadata={
        "provider_loan_id": "shared",
        "adept_binding": {
            "adept_profile_id": "historical-hb", "adept_loan_id": "ADOBE-COMPLETED",
        },
    })
    failed = acquisition(profile="wrong-profile", status="failed", metadata={
        "provider_loan_id": "shared",
        "adept_binding": {
            "adept_profile_id": "wrong-profile", "adept_loan_id": "ADOBE-FAILED",
        },
    })
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[completed, failed],
    )
    first = build_paris_adept_audit(storage)
    storage.ledger.reverse()
    second = build_paris_adept_audit(storage)
    for report in (first, second):
        loan = report["loans"][0]
        row = loan["exposures"][0]
        assert row["acquisition_statuses"] == ["complete", "failed"]
        assert loan["ledger_status_ambiguous"] is True
        assert loan["recorded_profile_conflict"] is False
        assert loan["adept_loan_reference_disagreement"] is False
        assert loan["origin_resolution"] == "single_recorded"
        assert row["recorded_adept_profile_ids"] == ["historical-hb"]
        assert "wrong-profile" not in repr(report)
        assert "ADOBE-FAILED" not in repr(report)


@pytest.mark.parametrize("status", ["attention_required", "failed", "pending", "cancelled"])
def test_noncomplete_acquisition_is_not_adept_origin_evidence(status):
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[acquisition(profile="unattested-proposed", status=status)],
    )
    report = build_paris_adept_audit(storage)
    loan = report["loans"][0]
    row = loan["exposures"][0]
    assert row["acquisition_status"] == status
    assert row.get("recorded_adept_profile_id") is None
    assert row.get("recorded_adept_loan_ref") is None
    assert loan["origin_resolution"] == "unknown"
    assert "unattested-proposed" not in repr(report)


def test_unknown_ledger_status_is_closed_and_never_leaks_sensitive_text():
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[acquisition(
            profile="proposed-only", status="PRIVATE-ACSM-STATUS-WITH-TOKEN"
        )],
    )
    report = build_paris_adept_audit(storage)
    row = report["loans"][0]["exposures"][0]
    assert row["acquisition_statuses"] == ["unknown"]
    assert row["acquisition_status"] == "unknown"
    assert report["loans"][0]["origin_resolution"] == "unknown"
    assert "PRIVATE-ACSM" not in repr(report)
    assert "proposed-only" not in repr(report)


def test_safe_next_review_for_reused_paris_loan_id_with_two_items():
    first = right("paris-a", "alice", "primary", item="book")
    second = right("paris-a", "alice", "primary", item="other-book")
    report = build_paris_adept_audit(FakeStorage(entitlements=[first, second]))
    loan = report["loans"][0]
    assert loan["multiple_connections"] is False
    assert loan["multiple_item_ids"] is True
    assert loan["requires_attention"] is True
    assert loan["review_reasons"] == ["paris_loan_id_reused"]
    assert loan["next_read_only_review"] == "compare_exact_loan_and_adobe_transactions"


def test_safe_next_review_for_two_completed_adobe_profiles():
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[
            acquisition(profile="profile-a"),
            acquisition(profile="profile-b"),
        ],
    )
    loan = build_paris_adept_audit(storage)["loans"][0]
    assert loan["recorded_profile_conflict"] is True
    assert "recorded_adobe_profiles_conflict" in loan["review_reasons"]
    assert loan["next_read_only_review"] == "compare_exact_loan_and_adobe_transactions"


def test_safe_next_review_prioritizes_unverified_explicit_binding():
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "linked")],
    )
    loan = build_paris_adept_audit(storage)["loans"][0]
    assert loan["review_reasons"] == ["source_binding_unverified"]
    assert loan["next_read_only_review"] == "inspect_paris_account_binding"


def test_configured_vs_historical_adobe_profile_review_does_not_authorize_return():
    historical = acquisition(profile="historical-hb", metadata={
        "provider_loan_id": "shared",
        "adept_binding": {
            "adept_profile_id": "historical-hb", "adept_loan_id": "PRIVATE-LOAN",
        },
    })
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[historical],
    )
    report = build_paris_adept_audit(storage)
    loan = report["loans"][0]
    assert loan["origin_resolution"] == "single_recorded"
    assert loan["recorded_profile_conflict"] is False
    assert loan["review_reasons"] == ["configured_vs_recorded_profile_differs"]
    assert loan["next_read_only_review"] == "compare_configured_vs_historical_profile"
    assert "PRIVATE-LOAN" not in repr(report)


def test_normal_one_connection_without_historical_adobe_claim_has_no_review_action():
    storage = FakeStorage(entitlements=[right("paris-a", "alice", "primary")])
    loan = build_paris_adept_audit(storage)["loans"][0]
    assert loan["requires_attention"] is False
    assert loan["review_reasons"] == []
    assert loan["next_read_only_review"] == "none"


def test_shared_paris_loan_with_two_configured_profiles_requests_exact_review():
    storage = FakeStorage(entitlements=[
        right("paris-a", "alice", "primary"),
        right("paris-b", "bob", "primary"),
    ])
    loan = build_paris_adept_audit(storage)["loans"][0]
    assert loan["configured_profile_divergence"] is True
    assert loan["recorded_profile_conflict"] is False
    assert "configured_profiles_diverge" in loan["review_reasons"]
    assert "multiple_paris_connections" in loan["review_reasons"]
    assert loan["next_read_only_review"] == "compare_exact_loan_and_adobe_transactions"
    assert loan["requires_attention"] is True


@pytest.mark.parametrize("executor,strategy", [
    ("provider", "loan_auto"),
    ("kobo-adept", "manual"),
    ("kobo", "loan_auto"),
    ("PRIVATE-TOKEN", "loan_auto"),
])
def test_completed_non_adobe_delivery_never_attests_historical_adept_origin(executor, strategy):
    row = acquisition(profile="NOT-ACTIVATED")
    row.update(executor=executor, strategy=strategy)
    row["metadata"]["adept_binding"]["adept_loan_id"] = "PRIVATE-LICENSE-IGNORED"
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[row],
    )
    report = build_paris_adept_audit(storage)
    loan = report["loans"][0]
    exposure = loan["exposures"][0]
    assert exposure["acquisition_status"] == "complete"
    assert "acquisition" in exposure["sources"]
    assert exposure.get("recorded_adept_profile_ids") is None
    assert exposure.get("recorded_adept_loan_refs") is None
    assert loan["origin_resolution"] == "unknown"
    assert "PRIVATE-LICENSE" not in repr(report)
    assert "NOT-ACTIVATED" not in repr(report)


def test_completed_adept_executor_keeps_legacy_historical_origin_visibility():
    record = acquisition(profile="actual-historical-profile")
    record.update(executor="kobo-adept", strategy="loan_auto")
    record["metadata"]["adept_binding"]["adept_loan_id"] = "LICENSE-RECORDED"
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[record],
    )
    exposure = build_paris_adept_audit(storage)["loans"][0]["exposures"][0]
    assert exposure["recorded_adept_profile_id"] == "actual-historical-profile"
    assert exposure["recorded_adept_loan_ref"] is not None
    # A legacy receipt lacking executor and strategy stays diagnostic only.
    del record["executor"], record["strategy"]
    legacy = build_paris_adept_audit(storage)["loans"][0]["exposures"][0]
    assert legacy["recorded_adept_profile_id"] == "actual-historical-profile"


@pytest.mark.parametrize("bad_id", [
    {"signed_url": "PRIVATE-URL"}, ["PRIVATE-LIST"], True, 42,
])
def test_malformed_provider_loan_id_never_becomes_copyable_json_group_key(bad_id):
    record = acquisition(profile="untrusted")
    record["metadata"]["provider_loan_id"] = bad_id
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[record],
    )
    report = build_paris_adept_audit(storage)
    assert report["summary"]["loan_groups"] == 1
    assert report["summary"]["rows_without_loan_id"] == 1
    assert report["loans"][0]["exposures"][0]["sources"] == ["entitlement"]
    assert "PRIVATE-" not in repr(report)
    assert "untrusted" not in repr(report)


def test_foreign_provider_row_never_pollutes_paris_adept_report():
    record = acquisition(profile="foreign-profile")
    record.update(provider_id="kobo", executor="kobo-adept", strategy="loan_auto")
    record["metadata"]["adept_binding"]["adept_loan_id"] = "PRIVATE-FOREIGN"
    storage = FakeStorage(
        entitlements=[right("paris-a", "alice", "primary")],
        acquisitions=[record],
    )
    report = build_paris_adept_audit(storage)
    assert report["summary"]["loan_groups"] == 1
    assert report["loans"][0]["exposures"][0]["sources"] == ["entitlement"]
    assert report["loans"][0]["origin_resolution"] == "unknown"
    assert "foreign-profile" not in repr(report)
    assert "PRIVATE-FOREIGN" not in repr(report)
