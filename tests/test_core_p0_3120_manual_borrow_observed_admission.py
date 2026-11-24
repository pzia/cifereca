"""#3120: ordinary admission consumes only fresh exact official observations."""
from dataclasses import replace
from time import time

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.plugin_api import BorrowerEligibilitySnapshot, BorrowerOperationEvidence
from cifereca.plugin_api.lending_capabilities import (
    BorrowerOperationEvidence as AccountEvidence,
)
from tests.test_paris_manual_borrow_dispatch import setup, attempt


def _snapshot(session, account, item, *, account_changes=None,
              edition_changes=None, age=0, epoch=None):
    branch = AccountEvidence(
        identity_id=account.identity_id, provider_id=account.provider_id,
        credential_id=account.credential_id, account_id=account.unique_id,
        branch=item.docbase, subscription="valid", remaining_loans=1,
        permission="allowed",
    )
    edition = BorrowerOperationEvidence(
        identity_id=account.identity_id, provider_id=account.provider_id,
        credential_id=account.credential_id, borrower_id=account.unique_id,
        docbase=item.docbase, record_id=item.record_id,
        source_collection=item.source_collection, account_state="active",
        quota_state="available", permission="allowed", item_offer="available",
    )
    session.borrower_eligibility_snapshot = BorrowerEligibilitySnapshot(
        observed_at=int(time()) - age,
        session_epoch=session._epoch if epoch is None else epoch,
        account_evidence=(() if account_changes is None else
                          (replace(branch, **account_changes),)),
        edition_evidence=(() if edition_changes is None else
                          (replace(edition, **edition_changes),)),
    )


@pytest.fixture
def harness(tmp_path, monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    service, session, account, item, state = setup(tmp_path)
    session._epoch = 1
    try:
        yield service, session, account, item, state
    finally:
        service.close()


@pytest.mark.parametrize(("account_changes", "edition_changes", "reason"), [
    ({"remaining_loans": 0}, None, "borrower_quota_exhausted"),
    ({"subscription": "renewal_pending"}, None, "borrower_subscription_blocked"),
    ({"subscription": "expired"}, None, "borrower_subscription_blocked"),
    ({"permission": "denied"}, None, "borrower_permission_denied"),
    (None, {"quota_state": "exhausted"}, "borrower_quota_exhausted"),
    (None, {"item_offer": "unavailable"}, "item_offer_unavailable"),
])
def test_fresh_exact_negative_blocks_display_and_new_claim(
    harness, account_changes, edition_changes, reason,
):
    service, session, account, item, state = harness
    _snapshot(session, account, item, account_changes=account_changes,
              edition_changes=edition_changes)
    assert not service.can_borrow_manual(item)
    assert service.manual_borrow_diagnostic(item) == {
        "local_action_possible": False,
        "reason_code": reason,
        "provider_eligibility": "blocked",
    }
    outcome = attempt(service, account, item)
    assert outcome["status"] == "blocked" and outcome["reason_code"] == reason
    assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
    assert state["calls"] == [] and state["mutations"] == 0


@pytest.mark.parametrize("changes", [
    {"account_changes": {"remaining_loans": 0}, "age": 301},
    {"account_changes": {"remaining_loans": 0}, "age": -30},
    {"account_changes": {"remaining_loans": 0}, "epoch": 2},
    {"account_changes": {"remaining_loans": 0, "identity_id": "alex"}},
    {"account_changes": {"remaining_loans": 0, "account_id": "linked"}},
    {"account_changes": {"remaining_loans": 0, "credential_id": "foreign"}},
    {"account_changes": {"remaining_loans": 0, "branch": "SYRACUSE"}},
    {"edition_changes": {"permission": "denied", "record_id": "other"}},
    {"edition_changes": {"permission": "denied", "source_collection": "other"}},
    {"account_changes": {"remaining_loans": None}},
])
def test_unknown_or_foreign_negative_does_not_veto_manual_action(harness, changes):
    service, session, account, item, state = harness
    _snapshot(session, account, item, **changes)
    assert service.can_borrow_manual(item)
    assert service.manual_borrow_diagnostic(item)["provider_eligibility"] == "unknown"
    assert state["calls"] == [] and state["mutations"] == 0
    assert attempt(service, account, item)["status"] == "confirmed"
    assert state["mutations"] == 1


def test_positive_observation_is_not_consent_and_unknown_replaces_old_denial(harness):
    service, session, account, item, state = harness
    _snapshot(session, account, item, account_changes={"remaining_loans": 0})
    assert not service.can_borrow_manual(item)
    _snapshot(session, account, item, account_changes={}, edition_changes={})
    assert service.can_borrow_manual(item)
    assert service.manual_borrow_diagnostic(item)["provider_eligibility"] == "observed"
    assert service.borrow_manual(item, expected_identity_id=account.identity_id,
                                 confirmed=False)["status"] == "blocked"
    _snapshot(session, account, item)
    assert service.manual_borrow_diagnostic(item)["provider_eligibility"] == "unknown"
    assert state["calls"] == [] and state["mutations"] == 0


def test_late_negative_after_claim_releases_only_unsent_attempt(harness, monkeypatch):
    service, session, account, item, state = harness
    original = service.storage.loan_borrow_claim_once

    def claim(*args, **kwargs):
        receipt = original(*args, **kwargs)
        _snapshot(session, account, item, account_changes={"remaining_loans": 0})
        return receipt

    monkeypatch.setattr(service.storage, "loan_borrow_claim_once", claim)
    assert attempt(service, account, item)["reason_code"] == "borrower_quota_exhausted"
    assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
    assert state["calls"] == [] and state["mutations"] == 0


def test_spent_claim_keeps_verification_precedence(harness):
    service, session, account, item, state = harness
    assert attempt(service, account, item)["status"] == "confirmed"
    _snapshot(session, account, item, account_changes={"remaining_loans": 0})
    assert attempt(service, account, item)["reason_code"] == "borrow_previous_attempt"
    assert service.storage.loan_borrow_state(borrow_claim_key(account, item))["status"] == "confirmed"
    assert state["mutations"] == 1
