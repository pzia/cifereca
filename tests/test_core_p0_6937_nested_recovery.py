"""#6937: explicit recovery cannot settle a caller-owned SQLite transaction."""
import sqlite3
from pathlib import Path

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_recovery_operator as operator
from cifereca.core import derived_variant_supervised_recovery as recovery
from cifereca.locking import InterProcessLock
from tests.test_core_p0_4520_derived_variant_supervised_recovery import _recover
from tests.test_core_p0_4520_recovery_operator import _setup, _review


@pytest.mark.parametrize("operation", ["direct", "review", "confirmed"])
@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_caller_transaction_survives_recovery_refusal(tmp_path, monkeypatch, operation, finish):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: lock_path)
    sql = db._connect()
    observer = sqlite3.connect(db.path)
    marker = "synthetic.6937.caller"
    original = db.metadata_get(reservation.key)
    stage, target = Path(intent.staged_path), Path(intent.candidate_path)
    try:
        review = _review(service, reservation)
        assert review is not None
        with InterProcessLock(lock_path) as lock:
            sql.execute("BEGIN IMMEDIATE")
            sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (marker, "pending"))
            if operation == "direct":
                with pytest.raises(journal.DerivedJournalConflict, match="nested_transaction"):
                    _recover(db, tmp_path, reservation, lock)
            elif operation == "review":
                assert _review(service, reservation) is None
            else:
                assert operator.execute_confirmed_linked_recovery(
                    service, review, confirmed=True, shared_lock=lock,
                )["status"] == "review_required"
            assert sql.in_transaction
            assert sql.execute("SELECT value FROM metadata WHERE key=?", (marker,)).fetchone() == ("pending",)
            assert observer.execute("SELECT value FROM metadata WHERE key=?", (marker,)).fetchone() is None
            assert observer.execute("SELECT value FROM metadata WHERE key=?", (reservation.key,)).fetchone() == (original,)
            assert stage.samefile(target)
            if finish == "commit":
                sql.commit()
            else:
                sql.rollback()
            assert observer.execute("SELECT value FROM metadata WHERE key=?", (marker,)).fetchone() == (
                ("pending",) if finish == "commit" else None)
            assert db.metadata_get(reservation.key) == original
            if operation == "confirmed":
                assert operator.execute_confirmed_linked_recovery(
                    service, review, confirmed=True, shared_lock=lock,
                )["status"] == "review_required"
            fresh = _review(service, reservation)
            assert fresh is not None
            assert operator.execute_confirmed_linked_recovery(
                service, fresh, confirmed=True, shared_lock=lock,
            )["status"] == "committed"
        assert journal.summarize_derived_journal(db)["committed_count"] == 1
        assert stage.samefile(target)
    finally:
        if sql.in_transaction:
            sql.rollback()
        observer.close()
        db.close()
