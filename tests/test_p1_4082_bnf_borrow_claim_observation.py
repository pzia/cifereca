"""P1 #4082: BnF one-shot claim can be inspected without rearming it.

All profiles, accounts, loans and bytes are synthetic. Genuine SQLite
Storage and Core borrow_claim_key; absolutely no network or borrow POST.
"""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.models import Account, SearchResult
from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.bnf_borrow_claim_observation import observe_bnf_borrow_claim
from cifereca.storage import Storage


def _account():
    return Account(
        identity_id="alice", provider_id="bnf-pret",
        credential_id="credential-a", unique_id="borrower-a",
    )


def _item(account):
    return SearchResult(
        id="bnf:test", record_id="record-a", docbase="BNF_PRET",
        medium="digital", title="Synthetic book", author=None,
        type="Livre numérique", format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="BNF_PRET", source_provider="bnf-pret",
        source_credential=account.credential_id,
    )


def _observe(db, account, item, identity_id="alice"):
    return observe_bnf_borrow_claim(
        storage=db, account=account, item=item,
        expected_identity_id=identity_id,
    )


def test_no_checkpoint_is_not_borrow_permission(tmp_path):
    db = Storage(tmp_path / "borrow.sqlite3")
    try:
        a = _account()
        state = _observe(db, a, _item(a))
        assert state.state == "no_checkpoint"
        assert state.claim_consumed is False
        assert state.retry_authorized is False
        assert state.provider_verified is False
        assert "borrower-a" not in repr(state)
        # No automatic mutation, no claim key minted in SQLite.
        assert db.loan_borrow_state(borrow_claim_key(a, _item(a))) is None
    finally:
        db.close()


@pytest.mark.parametrize(("state", "accepted", "loan", "verified"), [
    ("request_sent", False, None, False),
    ("submitted", True, None, False),
    ("verification_unavailable", False, None, False),
    ("blocked", False, None, False),
    ("confirmed", True, "loan-synthetic", True),
])
def test_closed_checkpoint_states_from_real_sqlite(
    tmp_path, state, accepted, loan, verified,
):
    db = Storage(tmp_path / "states.sqlite3")
    try:
        a = _account()
        item = _item(a)
        key = borrow_claim_key(a, item)
        assert db.loan_borrow_claim_once(key, "synthetic-operation")[0]
        if state != "request_sent":
            db.loan_borrow_finish(
                key, "synthetic-operation", status=state,
                accepted=accepted, verified_loan_id=loan,
            )
        prior = db.metadata_get(key)
        result = _observe(db, a, item)
        assert result.state == state
        assert result.claim_consumed is True
        assert result.provider_verified is verified
        assert result.retry_authorized is False
        assert "synthetic-operation" not in repr(result)
        assert "loan-synthetic" not in repr(result)
        assert db.metadata_get(key) == prior  # observation is read-only
        assert db.loan_borrow_claim_once(key, "second-attempt")[0] is False
    finally:
        db.close()


@pytest.mark.parametrize("payload", [
    "broken json",
    "",
    "{" + "}" * 5000,
    json.dumps({"schema": True, "operation": "loan.borrow",
                "operation_id": "spoof", "status": "confirmed",
                "request_accepted": True,
                "verification": "positive_active_loan"}),
    json.dumps({"schema": 1, "operation": "loan.borrow",
                "operation_id": "spoof", "status": "confirmed",
                "request_accepted": False,
                "verification": "positive_active_loan"}),
    json.dumps({"schema": 1, "operation": "loan.borrow",
                "operation_id": "spoof", "status": "unknown"}),
    json.dumps({"schema": 1, "operation": "loan.borrow",
                "operation_id": "spoof", "status": ["confirmed"]}),
    json.dumps({"schema": 1, "operation": "loan.borrow",
                "operation_id": "spoof", "status": {"danger": "confirmed"}}),
    json.dumps({"schema": 1, "operation": "loan.borrow",
                "operation_id": "spoof", "status": "confirmed",
                "request_accepted": True, "verification": "positive_active_loan",
                "verified_loan_id": "invalid\\nloan"}),
])
def test_corrupt_consumer_visible_state_is_consumed_not_retryable(
    tmp_path, payload,
):
    db = Storage(tmp_path / "corrupt.sqlite3")
    try:
        a, item = _account(), _item(_account())
        key = borrow_claim_key(a, item)
        db.metadata_set(key, payload)
        result = _observe(db, a, item)
        assert result.state == "opaque_consumed"
        assert result.claim_consumed is True
        assert result.provider_verified is False
        assert result.retry_authorized is False
        assert db.loan_borrow_claim_once(key, "new-post")[0] is False
        assert "spoof" not in repr(result)
    finally:
        db.close()


def test_exact_identity_account_edition_isolation_and_restart(tmp_path):
    filename = tmp_path / "identity.sqlite3"
    db = Storage(filename)
    a = _account()
    original = _item(a)
    key = borrow_claim_key(a, original)
    try:
        db.loan_borrow_claim_once(key, "borrow-once")
        assert _observe(db, a, original).state == "request_sent"
        assert _observe(db, a, replace(original, record_id="other")).state == "no_checkpoint"
        assert _observe(db, replace(a, unique_id="other"), original).state == "no_checkpoint"
        assert _observe(db, replace(a, credential_id="other"),
                        replace(original, source_credential="other")).state == "no_checkpoint"
        assert _observe(db, a, original, identity_id="other").state == "scope_unverified"
        assert _observe(db, a, replace(original, docbase="OTHER")).state == "scope_unverified"
        assert _observe(db, a, replace(original, medium="physical")).state == "scope_unverified"
        assert _observe(db, a, replace(original, source_provider="paris")).state == "scope_unverified"
    finally:
        db.close()
    reopened = Storage(filename)
    try:
        assert _observe(reopened, a, original).state == "request_sent"
        assert reopened.loan_borrow_claim_once(key, "replayed")[0] is False
    finally:
        reopened.close()


def test_metadata_read_failure_is_unknown_not_unspent():
    a = _account()
    class Unavailable:
        def metadata_get(self, key):
            raise OSError("no SQLite")
    result = _observe(Unavailable(), a, _item(a))
    assert result.state == "read_unavailable"
    assert result.claim_consumed is None
    assert result.retry_authorized is False
