"""#4082: real SQLite inter-process contention and ABA replay fences.

No provider, network or mutation outside temporary test SQLite files.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import sqlite3
import time

from cifereca.storage import Storage
from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_p1_4082_bnf_borrow_claim_observation import _account, _item


def _reserve(path: str, key: str, operation: str) -> bool:
    # Retry only transient schema-initialization contention, never CAS failure.
    for attempt in range(12):
        try:
            storage = Storage(Path(path))
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 11:
                raise
            time.sleep(0.05 * (attempt + 1))
    try:
        return storage.loan_borrow_claim_next_cycle(
            key, "first", "loan-1", operation,
        )[0]
    finally:
        storage.close()


def test_competing_processes_cannot_reserve_same_second_cycle(tmp_path):
    path = tmp_path / "process-race.sqlite3"
    db = Storage(path)
    key = borrow_claim_key(_account(), _item(_account()))
    try:
        assert db.loan_borrow_claim_once(key, "first")[0]
        assert db.loan_borrow_finish(
            key, "first", status="confirmed", accepted=True,
            verified_loan_id="loan-1",
        ) is not None
        with ProcessPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_reserve, str(path), key, f"cycle-{i}")
                       for i in range(4)]
            outcomes = [f.result(timeout=30) for f in futures]
        assert outcomes.count(True) == 1
        assert outcomes.count(False) == 3
        current = db.loan_borrow_state(key)
        assert current["cycle_generation"] == 2
        assert current["status"] == "request_sent"
        assert not db.loan_borrow_claim_next_cycle(
            key, "first", "loan-1", "late-replay",
        )[0]
    finally:
        db.close()


def test_aba_old_operation_cannot_finish_or_rearm_new_generation(tmp_path):
    db = Storage(tmp_path / "aba.sqlite3")
    key = borrow_claim_key(_account(), _item(_account()))
    try:
        assert db.loan_borrow_claim_once(key, "first")[0]
        assert db.loan_borrow_finish(
            key, "first", status="confirmed", accepted=True,
            verified_loan_id="loan-1",
        ) is not None
        assert db.loan_borrow_claim_next_cycle(
            key, "first", "loan-1", "second",
        )[0]
        before = db.metadata_get(key)
        assert db.loan_borrow_finish(
            key, "first", status="blocked",
        ) is None
        assert not db.loan_borrow_release_unsent(key, "first")
        assert db.metadata_get(key) == before
        assert db.loan_borrow_finish(
            key, "second", status="confirmed", accepted=True,
            verified_loan_id="loan-2",
        ) is not None
        assert not db.loan_borrow_claim_next_cycle(
            key, "second", "loan-2", "first",
        )[0]
        assert not db.loan_borrow_claim_next_cycle(
            key, "second", "loan-2", "second",
        )[0]
        assert db.loan_borrow_state(key)["cycle_generation"] == 2
    finally:
        db.close()
