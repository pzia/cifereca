"""#6937: read-only diagnostics must not roll back or commit caller work."""
from pathlib import Path
import sqlite3

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core.derived_variant_region_review import review_derived_private_region
from cifereca.core.derived_variant_review_queue import pending_derived_review_page
from cifereca.core.derived_variant_stock_review import review_derived_staging_stock
from tests.test_core_p0_4520_recovery_operator import _setup


@pytest.mark.parametrize("operation", ["summary", "queue", "stock", "region"])
@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_review_with_caller_transaction_preserves_uncommitted_work(tmp_path, operation, finish):
    service, intent, reservation = _setup(tmp_path)
    storage = service.storage
    db = storage._connect()
    sentinel = "synthetic.caller.write"

    def outside_value():
        other = sqlite3.connect(storage.path)
        try:
            return other.execute("SELECT value FROM metadata WHERE key=?", (sentinel,)).fetchone()
        finally:
            other.close()

    try:
        prior = storage.metadata_get(reservation.key)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (sentinel, "pending"))
        if operation == "summary":
            with pytest.raises(journal.DerivedJournalConflict, match="derived_journal_nested_transaction"):
                journal.summarize_derived_journal(storage)
        elif operation == "queue":
            with pytest.raises(journal.DerivedJournalConflict, match="derived_review_queue_nested_transaction"):
                pending_derived_review_page(storage, identity_id="id1")
        else:
            kwargs = {"library_root": tmp_path, "identity_id": "id1"}
            result = (review_derived_staging_stock(storage, **kwargs) if operation == "stock" else
                      review_derived_private_region(storage, explicitly_requested=True, **kwargs))
            assert result["status"] == "unavailable"
            assert result["destructive_cleanup_allowed"] is False
        assert db.in_transaction
        assert db.execute("SELECT value FROM metadata WHERE key=?", (sentinel,)).fetchone() == ("pending",)
        assert db.execute("SELECT value FROM metadata WHERE key=?", (reservation.key,)).fetchone() == (prior,)
        assert outside_value() is None
        if finish == "commit":
            db.commit()
        else:
            db.rollback()
        assert outside_value() == (("pending",) if finish == "commit" else None)
        assert storage.metadata_get(reservation.key) == prior
        assert journal.summarize_derived_journal(storage)["pending_count"] == 1
        assert len(pending_derived_review_page(storage, identity_id="id1")["reservations"]) == 1
        assert review_derived_staging_stock(
            storage, library_root=tmp_path, identity_id="id1",
        )["status"] == "journal_entries_observed"
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        if db.in_transaction:
            db.rollback()
        storage.close()
