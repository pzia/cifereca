"""#4708: synthetic SQLite transfer/restore, no installed writer route."""
import pytest

from cifereca.config import Config
from cifereca.core import missing_placement_archive_sql as archive
from cifereca.core.destructive_cleanup_policy import cleanup_review_epoch
from cifereca.storage import Storage
from tests.test_core_p0_4708_historical_missing_report import _setup


@pytest.fixture
def rig(tmp_path):
    database, config_path, root, ids = _setup(tmp_path)
    store = Storage(database)
    config = Config(config_path)
    config.set("maintenance.destructive_cleanup_enabled", True)
    config.save()
    db = store._connect()
    db.execute(archive._DDL)
    db.execute("PRAGMA synchronous=FULL")
    pid = ids["unlinked.epub"]
    row = archive._live_row(db, pid)
    payload, digest = archive._encode_archive_row(row)
    args = dict(expected_payload=payload, expected_digest=digest, config=config,
                expected_epoch=cleanup_review_epoch(config), confirmed=True,
                scope_current=lambda: True, max_records=4, max_bytes=1024 * 1024)
    try:
        yield store, db, row, args
    finally:
        if db.in_transaction:
            db.rollback()
        store.close()


def test_transfer_and_restore_are_exact_and_outer_commit_owned(rig):
    store, db, row, args = rig
    db.execute("BEGIN IMMEDIATE")
    archive._change(db, **args, restore=False)
    assert db.in_transaction
    assert archive._live_row(db, row[0]) is None
    assert db.execute("SELECT payload FROM " + archive._TABLE).fetchone() == (args["expected_payload"],)
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    archive._change(db, **args, restore=True)
    assert db.in_transaction and archive._live_row(db, row[0]) == row
    assert db.execute("SELECT count(*) FROM " + archive._TABLE).fetchone() == (0,)
    db.commit()
    assert archive._live_row(db, row[0]) == row


def test_outer_rollback_preserves_original(rig):
    _, db, row, args = rig
    db.execute("BEGIN IMMEDIATE")
    archive._change(db, **args, restore=False)
    db.rollback()
    assert archive._live_row(db, row[0]) == row
    assert db.execute("SELECT count(*) FROM " + archive._TABLE).fetchone() == (0,)


def test_scope_revocation_rolls_back_savepoint_without_committing_caller(rig):
    _, db, row, args = rig
    calls = 0

    def current():
        nonlocal calls
        calls += 1
        return calls < 3

    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT INTO metadata(key,value) VALUES('test.archive.outer','private')")
    with pytest.raises(archive.ArchiveRefused, match="scope_or_confirmation"):
        archive._change(db, **{**args, "scope_current": current}, restore=False)
    assert db.in_transaction and archive._live_row(db, row[0]) == row
    assert db.execute("SELECT count(*) FROM " + archive._TABLE).fetchone() == (0,)
    assert db.execute("SELECT value FROM metadata WHERE key='test.archive.outer'").fetchone() == ("private",)
    db.rollback()


@pytest.mark.parametrize("override,reason", [
    ({"confirmed": False}, "scope_or_confirmation"),
    ({"max_records": 0}, "capacity_unconfigured"),
    ({"max_bytes": 1}, "capacity_exhausted"),
    ({"expected_epoch": "stale"}, "scope_or_confirmation"),
])
def test_refusal_preserves_row(rig, override, reason):
    _, db, row, args = rig
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(archive.ArchiveRefused, match=reason):
        archive._change(db, **{**args, **override}, restore=False)
    assert archive._live_row(db, row[0]) == row
    assert db.execute("SELECT count(*) FROM " + archive._TABLE).fetchone() == (0,)
    db.rollback()


def test_schema_extension_is_not_silently_lost(rig):
    _, db, row, args = rig
    db.execute("ALTER TABLE placements ADD COLUMN future_value TEXT")
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(archive.ArchiveRefused, match="schema_changed"):
        archive._change(db, **args, restore=False)
    assert archive._live_row(db, row[0]) == row
    db.rollback()


def test_trigger_failure_refuses_before_any_mutation(rig):
    _, db, row, args = rig
    db.execute("CREATE TRIGGER test_archive BEFORE DELETE ON placements "
               "BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(archive.ArchiveRefused, match="trigger_present"):
        archive._change(db, **args, restore=False)
    assert archive._live_row(db, row[0]) == row
    db.rollback()


def test_restore_collision_preserves_archive(rig):
    _, db, row, args = rig
    db.execute("BEGIN IMMEDIATE")
    archive._change(db, **args, restore=False)
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT INTO placements(" + ",".join(archive._COLUMNS) +
               ") VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("other", *row[1:]))
    with pytest.raises(archive.ArchiveRefused, match="restore_collision"):
        archive._change(db, **args, restore=True)
    assert db.execute("SELECT count(*) FROM " + archive._TABLE).fetchone() == (1,)
    db.rollback()


def test_reconcile_exact_states_and_ambiguous_commit(rig):
    _, db, row, args = rig
    proof = {k: args[k] for k in ("expected_payload", "expected_digest", "max_records", "max_bytes")}
    assert archive._reconcile(db, **proof) == "live_exact"
    db.execute("BEGIN IMMEDIATE")
    archive._change(db, **args, restore=False)
    assert archive._reconcile(db, **proof) == "unverified"
    assert db.in_transaction
    db.commit()
    assert archive._reconcile(db, **proof) == "archive_exact"
    assert not db.in_transaction
    db.execute("BEGIN IMMEDIATE")
    archive._change(db, **args, restore=True)
    db.rollback()
    assert archive._reconcile(db, **proof) == "archive_exact"


def test_reconcile_corrupted_archive_is_not_authority(rig):
    _, db, row, args = rig
    proof = {k: args[k] for k in ("expected_payload", "expected_digest", "max_records", "max_bytes")}
    db.execute("BEGIN IMMEDIATE")
    archive._change(db, **args, restore=False)
    db.commit()
    db.execute("UPDATE " + archive._TABLE + " SET payload=?", (b"corrupt",))
    db.commit()
    assert archive._reconcile(db, **proof) == "unverified"
    assert not db.in_transaction
    assert archive._live_row(db, row[0]) is None
