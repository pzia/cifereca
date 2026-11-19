"""#4708: restart and ambiguous outcomes without writer/retry."""
import pytest

from cifereca.core import missing_placement_archive_recovery as recovery
from cifereca.core import missing_placement_archive_sql as archive
from cifereca.storage import Storage
from tests.test_core_p0_4708_archive_sql import rig


def _review(store, row, args, **changes):
    options = {k: args[k] for k in (
        "expected_payload", "expected_digest", "max_records", "max_bytes",
    )}
    options.update(identity_id=row[3], explicitly_requested=True)
    options.update(changes)
    return recovery.review_archive_transition(store, **options)


def test_restart_reads_exact_committed_archive_and_restored_live_row(rig):
    store, db, row, args = rig
    assert _review(store, row, args)["status"] == "live_exact_observed"
    db.execute("BEGIN IMMEDIATE")
    archive._change(db, **args, restore=False)
    assert _review(store, row, args)["status"] == "unavailable"
    assert db.in_transaction
    db.commit()
    reopened = Storage(store.path)
    try:
        result = _review(reopened, row, args)
        assert result["status"] == "archive_exact_observed"
        assert result["automatic_retry_allowed"] is False
        assert result["restore_authorized"] is False
        assert row[2] not in repr(result) and row[0] not in repr(result)
    finally:
        reopened.close()
    db.execute("BEGIN IMMEDIATE")
    archive._change(db, **args, restore=True)
    db.commit()
    assert _review(store, row, args)["status"] == "live_exact_observed"


@pytest.mark.parametrize("state", ["both", "neither", "changed", "path_collision"])
def test_conflicting_or_missing_witness_preserves_without_retry(rig, state):
    store, db, row, args = rig
    db.execute("BEGIN IMMEDIATE")
    if state == "both":
        db.execute(f"INSERT INTO {archive._TABLE} VALUES(?,?,?,?,?)",
                   (row[0], row[3], row[2], args["expected_digest"], args["expected_payload"]))
    elif state == "neither":
        db.execute("DELETE FROM placements WHERE placement_id=?", (row[0],))
    elif state == "changed":
        db.execute("UPDATE placements SET updated_at=updated_at+1 WHERE placement_id=?", (row[0],))
    else:
        archive._change(db, **args, restore=False)
        db.execute("INSERT INTO placements(" + ",".join(archive._COLUMNS) +
                   ") VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("other", *row[1:]))
    db.commit()
    before = db.total_changes
    result = _review(store, row, args)
    assert result["status"] == "ambiguous_preserve"
    assert not result["archive_authorized"] and not result["restore_authorized"]
    assert not result["automatic_retry_allowed"] and db.total_changes == before


def test_wrong_owner_or_invalid_witness_or_no_request_is_unavailable(rig):
    store, db, row, args = rig
    for options in ({"identity_id": "foreign"}, {"explicitly_requested": False},
                    {"expected_digest": "wrong"}, {"max_records": 0}):
        assert _review(store, row, args, **options)["status"] == "unavailable"


def test_corrupted_archive_is_not_recoverable_authority(rig):
    store, db, row, args = rig
    db.execute("BEGIN IMMEDIATE")
    archive._change(db, **args, restore=False)
    db.commit()
    db.execute("UPDATE " + archive._TABLE + " SET payload=?", (b"corrupt",))
    db.commit()
    before = db.total_changes
    result = _review(store, row, args)
    assert result["status"] == "unavailable"
    assert result["automatic_retry_allowed"] is False
    assert not db.in_transaction and db.total_changes == before
    assert archive._live_row(db, row[0]) is None
