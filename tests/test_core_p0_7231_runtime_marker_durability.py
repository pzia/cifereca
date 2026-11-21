"""#7231: exact live non-submission only; SQLite durability and isolation."""
from dataclasses import replace
import sqlite3

import pytest

from cifereca.core import canonical_runtime_attempt as runtime
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register, store


@pytest.mark.parametrize("phase", ["runtime", "purchase_acsm"])
def test_runtime_refusal_commit_is_durable_and_exact(store, tmp_path, monkeypatch, phase):
    source, _ = new_source()
    sibling = replace(source, right_id="sibling")
    register(store, "A", "seller", "card-A")
    lock = tmp_path / "acquisition.lock"
    monkeypatch.setattr("cifereca.core.canonical_recovery.acquisition_lock_file", lambda: lock)
    db = store._connect()
    previous = db.execute("PRAGMA synchronous").fetchone()
    seen = []
    db.set_trace_callback(seen.append)
    try:
        with InterProcessLock(lock) as lease:
            op = runtime.begin_runtime_attempt(store, source=source, offer_id="offer",
                                               shared_lock=lease, phase=phase)
            runtime.begin_runtime_attempt(store, source=sibling, offer_id="offer",
                                          shared_lock=lease, phase=phase)
            for changed_offer, changed_op, reason in (
                ("offer", "", "release_invalid"),
                ("offer", "a" * 32 if op != "a" * 32 else "b" * 32, "changed"),
                ("changed", op, "changed"),
            ):
                with pytest.raises(ValueError, match=reason):
                    runtime.release_unsubmitted_runtime_attempt(
                        store, source=source, offer_id=changed_offer,
                        operation_id=changed_op, shared_lock=lease,
                    )
                assert runtime.pending_runtime_attempt(store, source)
                assert db.execute("PRAGMA synchronous").fetchone() == previous
            runtime.release_unsubmitted_runtime_attempt(
                store, source=source, offer_id="offer", operation_id=op, shared_lock=lease,
            )
            assert db.execute("PRAGMA synchronous").fetchone() == previous
            reopened = Storage(store.path)
            try:
                assert not runtime.pending_runtime_attempt(reopened, source)
                assert runtime.pending_runtime_attempt(reopened, sibling)
            finally:
                reopened.close()
        assert sum("PRAGMA SYNCHRONOUS=FULL" in line.upper() for line in seen) == 5
    finally:
        db.set_trace_callback(None)


def test_runtime_refusal_does_not_settle_caller_transaction(store, tmp_path, monkeypatch):
    source, _ = new_source()
    register(store, "A", "seller", "card-A")
    lock = tmp_path / "acquisition.lock"
    monkeypatch.setattr("cifereca.core.canonical_recovery.acquisition_lock_file", lambda: lock)
    db = store._connect()
    other = sqlite3.connect(store.path)
    try:
        with InterProcessLock(lock) as lease:
            op = runtime.begin_runtime_attempt(store, source=source, offer_id="offer", shared_lock=lease)
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO metadata(key,value) VALUES('synthetic.caller','pending')")
            with pytest.raises(ValueError, match="nested_transaction"):
                runtime.release_unsubmitted_runtime_attempt(
                    store, source=source, offer_id="offer", operation_id=op, shared_lock=lease,
                )
            assert db.in_transaction
            assert other.execute("SELECT value FROM metadata WHERE key='synthetic.caller'").fetchone() is None
            assert other.execute("SELECT value FROM metadata WHERE key=?", (runtime._key(source),)).fetchone()
            db.rollback()
            assert runtime.pending_runtime_attempt(store, source)
    finally:
        if db.in_transaction:
            db.rollback()
        other.close()
