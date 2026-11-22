"""#6937: durable isolated v3 admission."""
from dataclasses import replace
from pathlib import Path
import os

import pytest

from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_private_region as region
from cifereca.core import derived_variant_supervised_recovery as recovery
from cifereca.core.derived_variant_private_admission import reserve_private_derived_publication
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage
from tests.test_core_p0_4520_derived_journal import _fixture


@pytest.fixture
def rig(tmp_path, monkeypatch):
    service, intent = _fixture(tmp_path)
    db = service.storage
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda: lock_path)
    try:
        with InterProcessLock(lock_path) as lock:
            common = dict(library_root=tmp_path, identity_id="id1", shared_lock=lock,
                          scope_current=lambda sql: sql.in_transaction, explicitly_requested=True)
            region.ensure_private_derived_region(db, placement_id=intent.parent_placement_id,
                                                  provision=True, **common)
            stage = Path(intent.parent_path).parent / region._REGION / "unique.epub"
            stage.write_bytes(b"new bytes")
            stage.chmod(0o600)
            s = stage.stat()
            private = replace(intent, staged_path=str(stage),
                              output_stamp=(s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns))
            binding = db.metadata_get(region._key("id1"))
            args = dict(common, intent=private, approved_binding_sha256=journal._receipt_digest(binding))
            yield db, private, stage, args
    finally:
        db.close()


def test_v3_admission_durable_and_legacy_writer_refuses(rig):
    db, intent, stage, args = rig
    sync = db._connect().execute("PRAGMA synchronous").fetchone()
    receipt = reserve_private_derived_publication(db, **args)
    assert db._connect().execute("PRAGMA synchronous").fetchone() == sync
    raw = db.metadata_get(receipt.key)
    assert journal._decode(receipt.key, raw) == intent
    other = Storage(db.path)
    try:
        assert other.metadata_get(receipt.key) == raw
        assert journal.summarize_derived_journal(other)["reserved_output_bytes"] == 9
        with other._lock, other._connect() as sql:
            sql.execute("BEGIN")
            with pytest.raises(journal.DerivedJournalConflict, match="private_region_review_required"):
                journal._read_pending_reservation(sql, receipt)
        with pytest.raises(journal.DerivedJournalConflict, match="slot_occupied"):
            reserve_private_derived_publication(other, **args)
    finally:
        other.close()
    assert stage.read_bytes() == b"new bytes"
    assert not Path(intent.candidate_path).exists() and len(db.placements()) == 1


@pytest.mark.parametrize("change", ["approval", "binding", "mode", "alias", "occupied",
                                     "bytes", "quota", "scope", "sql_fault"])
def test_v3_refusal_retains_bytes(rig, monkeypatch, change):
    db, intent, stage, args = rig
    if change == "approval":
        args["explicitly_requested"] = False
    elif change == "binding":
        args["approved_binding_sha256"] = "0" * 64
    elif change == "mode":
        stage.chmod(0o644)
    elif change == "alias":
        os.link(stage, stage.with_name("alias.epub"))
    elif change == "occupied":
        Path(intent.candidate_path).write_bytes(b"other")
    elif change == "bytes":
        stage.write_bytes(b"bad bytes")
    elif change == "quota":
        monkeypatch.setattr(journal, "_MAX_RESERVED_OUTPUT_BYTES", 8)
    elif change == "scope":
        args["scope_current"] = lambda _sql: False
    else:
        with db._lock, db._connect() as sql:
            sql.execute("CREATE TRIGGER synthetic_v3_abort BEFORE INSERT ON metadata "
                        "WHEN NEW.key LIKE 'derived.publication.pending.v2:%' "
                        "BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    before = stage.read_bytes()
    with pytest.raises(journal.DerivedJournalConflict):
        reserve_private_derived_publication(db, **args)
    assert journal.summarize_derived_journal(db)["count"] == 0
    assert stage.read_bytes() == before and len(db.placements()) == 1


def test_scope_revoked_after_insert_rolls_back(rig):
    db, intent, stage, args = rig
    key = journal._slot(intent)
    args["scope_current"] = lambda sql: sql.execute("SELECT 1 FROM metadata WHERE key=?", (key,)).fetchone() is None
    with pytest.raises(journal.DerivedJournalConflict):
        reserve_private_derived_publication(db, **args)
    assert db.metadata_get(key) is None
    assert stage.read_bytes() == b"new bytes"
    assert not Path(intent.candidate_path).exists()


def _recover_args(args, receipt):
    return {k: v for k, v in args.items() if k != "intent"} | {
        "reservation": receipt, "approved_receipt_sha256": receipt.receipt_sha256,
    }


def test_private_already_linked_recovery_uses_common_writer_after_restart(rig):
    from cifereca.core.derived_variant_private_recovery import recover_private_linked_derived_publication
    db, intent, stage, args = rig
    receipt = reserve_private_derived_publication(db, **args)
    raw = db.metadata_get(receipt.key)
    target = Path(intent.candidate_path)
    # Synthetic interrupted physical publication: the recovery operator
    # itself must never create a link or reconstruct transformation facts.
    os.link(stage, target)
    before = journal.summarize_derived_journal(db)
    other = Storage(db.path)
    try:
        result = recover_private_linked_derived_publication(other, **_recover_args(args, receipt))
        assert result == {"status": "committed", "destructive_cleanup_allowed": False}
        assert other.metadata_get(receipt.key) is None
        assert other.metadata_get(journal._COMMITTED + receipt.receipt_sha256) == raw
        assert journal._decode(journal._COMMITTED + receipt.receipt_sha256, raw) == intent
        after = journal.summarize_derived_journal(other)
        assert after["count"] == before["count"] == 1
        assert after["reserved_output_bytes"] == before["reserved_output_bytes"] == 9
        assert after["committed_count"] == 1
        assert len(other.placements()) == 2 and len(other.artifact_variants()) == 1
        with other._lock, other._connect() as sql:
            assert sql.execute("SELECT count(*) FROM origin_links WHERE origin_kind='derived'").fetchone() == (1,)
        with pytest.raises(journal.DerivedJournalConflict):
            recover_private_linked_derived_publication(other, **_recover_args(args, receipt))
        assert stage.samefile(target) and target.stat().st_nlink == 2
        assert target.read_bytes() == b"new bytes"
    finally:
        other.close()


@pytest.mark.parametrize("case", ["unconfirmed", "binding", "unlinked", "extra_link",
                                  "replaced_target", "mode", "marker", "scope", "legacy",
                                  "late_sql"])
def test_private_recovery_failures_preserve_pending_and_files(rig, monkeypatch, case):
    from cifereca.core.derived_variant_private_recovery import recover_private_linked_derived_publication
    from cifereca.core.derived_variant_publication import DerivedPublicationConflict
    db, intent, stage, args = rig
    receipt = reserve_private_derived_publication(db, **args)
    raw = db.metadata_get(receipt.key)
    target = Path(intent.candidate_path)
    options = _recover_args(args, receipt)
    if case != "unlinked":
        os.link(stage, target)
    if case == "unconfirmed":
        options["explicitly_requested"] = False
    elif case == "binding":
        options["approved_binding_sha256"] = "0" * 64
    elif case == "extra_link":
        os.link(stage, stage.with_name("extra.epub"))
    elif case == "replaced_target":
        target.unlink()
        target.write_bytes(stage.read_bytes())
    elif case == "mode":
        stage.chmod(0o644)
    elif case == "marker":
        (stage.parent / region._MARKER).write_bytes(b"0" * 32)
    elif case == "scope":
        options["scope_current"] = lambda sql: False
    elif case == "late_sql":
        with db._lock, db._connect() as sql:
            sql.execute("CREATE TRIGGER synthetic_commit_abort BEFORE UPDATE OF key ON metadata "
                        "WHEN NEW.key LIKE 'derived.publication.committed.v2:%' "
                        "BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        # The real SQLite error propagates just as it does for v2; the
        # surrounding common publication transaction must roll it back.
    if case == "legacy":
        options.pop("approved_binding_sha256")
        options.pop("explicitly_requested")
        operation = recovery.recover_linked_derived_publication
    else:
        operation = recover_private_linked_derived_publication
    import sqlite3
    before = stage.read_bytes()
    with pytest.raises((journal.DerivedJournalConflict, DerivedPublicationConflict, sqlite3.Error)):
        operation(db, **options)
    assert db.metadata_get(receipt.key) == raw
    assert journal.summarize_derived_journal(db)["pending_count"] == 1
    assert len(db.placements()) == 1 and not db.artifact_variants()
    assert stage.read_bytes() == before
    if case == "unlinked":
        assert not target.exists()
    else:
        assert target.read_bytes() == before
