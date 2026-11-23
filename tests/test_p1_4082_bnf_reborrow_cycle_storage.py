"""#4082: versioned reborrow CAS preserves history and blocks ambiguous POSTs.

Only real local SQLite storage is used; no provider requests or borrowing.
"""
from __future__ import annotations

from threading import Barrier, Thread

import pytest

from cifereca.storage import Storage
from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_p1_4082_bnf_borrow_claim_observation import _account, _item


def _confirmed(db, account=None, *, op="first", loan="loan-1"):
    account = account or _account()
    key = borrow_claim_key(account, _item(account))
    assert db.loan_borrow_claim_once(key, op)[0]
    assert db.loan_borrow_finish(
        key, op, status="confirmed", accepted=True, verified_loan_id=loan,
    )["status"] == "confirmed"
    return key


def test_second_manual_cycle_is_atomic_and_preserves_original_checkpoint(tmp_path):
    path = tmp_path / "cycles.sqlite3"
    db = Storage(path)
    try:
        key = _confirmed(db)
        original = db.metadata_get(key)
        assert not db.loan_borrow_claim_next_cycle(key, "wrong", "loan-1", "second")[0]
        assert not db.loan_borrow_claim_next_cycle(key, "first", "loan-2", "second")[0]
        ok, receipt = db.loan_borrow_claim_next_cycle(key, "first", "loan-1", "second")
        assert ok and receipt["status"] == "request_sent"
        assert receipt["cycle_generation"] == 2
        assert receipt["previous_operation_id"] == "first"
        assert db.loan_borrow_claim_once(key, "unexpected-third")[0] is False
        assert db.loan_borrow_claim_next_cycle(key, "first", "loan-1", "third")[0] is False
        history = "loan.borrow.history.v1:" + key.split(":")[-1] + ":1"
        assert db.metadata_get(history) == original
        assert db.loan_borrow_finish(
            key, "second", status="confirmed", accepted=True,
            verified_loan_id="loan-2",
        )["status"] == "confirmed"
    finally:
        db.close()
    db = Storage(path)
    try:
        assert db.loan_borrow_state(key)["cycle_generation"] == 2
        assert db.metadata_get(history) == original
        ok, receipt = db.loan_borrow_claim_next_cycle(
            key, "second", "loan-2", "third")
        assert ok and receipt["cycle_generation"] == 3
        assert db.metadata_get("loan.borrow.history.v1:" + key.split(":")[-1] + ":2")
        assert db.loan_borrow_claim_next_cycle(key, "second", "loan-2", "fourth")[0] is False
    finally:
        db.close()


def test_pre_post_abort_restores_confirmed_fence_instead_of_deleting(tmp_path):
    db = Storage(tmp_path / "restore.sqlite3")
    try:
        key = _confirmed(db)
        original = db.metadata_get(key)
        assert db.loan_borrow_claim_next_cycle(key, "first", "loan-1", "second")[0]
        assert not db.loan_borrow_release_unsent(key, "wrong")
        assert db.loan_borrow_release_unsent(key, "second")
        assert db.metadata_get(key) == original
        assert db.loan_borrow_state(key)["status"] == "confirmed"
        assert db.loan_borrow_claim_once(key, "bypass")[0] is False
        assert db.loan_borrow_claim_next_cycle(key, "first", "loan-1", "third")[0]
        assert db.loan_borrow_finish(
            key, "third", status="verification_unavailable",
        )["status"] == "verification_unavailable"
        assert not db.loan_borrow_release_unsent(key, "third")
        assert db.loan_borrow_claim_next_cycle(
            key, "third", "loan-1", "fourth")[0] is False
    finally:
        db.close()


@pytest.mark.parametrize("state", [
    "request_sent", "submitted", "verification_unavailable", "blocked",
])
def test_ambiguous_or_unconfirmed_previous_cycle_never_rearmed(tmp_path, state):
    db = Storage(tmp_path / ("blocked-" + state + ".sqlite3"))
    try:
        key = borrow_claim_key(_account(), _item(_account()))
        assert db.loan_borrow_claim_once(key, "prior")[0]
        if state != "request_sent":
            assert db.loan_borrow_finish(
                key, "prior", status=state,
                accepted=(state == "submitted"),
            ) is not None
        assert db.loan_borrow_claim_next_cycle(key, "prior", "loan-1", "new")[0] is False
    finally:
        db.close()


def test_concurrent_rearm_allows_only_one_process_and_is_scope_local(tmp_path):
    path = tmp_path / "race.sqlite3"
    first, second = Storage(path), Storage(path)
    try:
        key = _confirmed(first)
        barrier = Barrier(2)
        outcomes = []
        def racer(storage, op):
            barrier.wait()
            outcomes.append(storage.loan_borrow_claim_next_cycle(
                key, "first", "loan-1", op)[0])
        threads = [
            Thread(target=racer, args=(first, "second-a")),
            Thread(target=racer, args=(second, "second-b")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            assert not t.is_alive()
        assert sorted(outcomes) == [False, True]
        assert first.loan_borrow_state(key)["cycle_generation"] == 2
    finally:
        first.close()
        second.close()
