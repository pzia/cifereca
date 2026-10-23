"""P0 #2565: a long offline audit must not publish out-of-date SQLite rights.

Only synthetic EPUBs and private/local SQLite fixtures; no network, provider,
ADEPT, book IDs, account names or source bytes in the public broker payload.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from cifereca.core import library_audit_broker as broker
from cifereca.core.library_audit import audit_library as real_audit_library
from cifereca.core.library_sqlite_freshness import sqlite_source_stamp
from tests.test_library_audit_broker import scoped


def test_source_stamp_detects_changes_in_db_wal_and_journal_without_opening_db(tmp_path):
    db = tmp_path / "PRIVATE.sqlite"
    db.write_bytes(b"S" * 512)
    start = sqlite_source_stamp(db)
    assert start is not None
    assert all(type(part) in (tuple, type(None)) for part in start)
    # A SQLite commit can replace or extend WAL while the main DB is untouched.
    wal = tmp_path / "PRIVATE.sqlite-wal"
    wal.write_bytes(b"WAL-1")
    with_wal = sqlite_source_stamp(db)
    assert with_wal is not None and with_wal != start
    wal.write_bytes(b"WAL-CHANGED-2")
    assert sqlite_source_stamp(db) != with_wal
    wal.unlink()
    journal = tmp_path / "PRIVATE.sqlite-journal"
    journal.write_bytes(b"open transaction")
    assert sqlite_source_stamp(db) != start
    journal.unlink()
    db.write_bytes(b"S" * 513)
    assert sqlite_source_stamp(db) != start
    # Directory stat values are aggregate-only, never a private path.
    for word in ("PRIVATE", str(tmp_path), "sqlite-wal", "S" * 20):
        assert word not in repr(start)


def test_source_stamp_rejects_symlinked_database_and_sidecar(tmp_path):
    db = tmp_path / "PRIVATE.sqlite"
    real = tmp_path / "OTHER.sqlite"
    real.write_bytes(b"S" * 512)
    db.symlink_to(real)
    assert sqlite_source_stamp(db) is None
    db.unlink()
    db.write_bytes(b"S" * 512)
    wal = tmp_path / "PRIVATE.sqlite-wal"
    wal.symlink_to(real)
    assert sqlite_source_stamp(db) is None
    wal.unlink()
    assert sqlite_source_stamp(db) is not None


def test_source_commit_during_inventory_is_partial_not_current_rights(scoped, tmp_path):
    service, database, root = scoped
    fixture_writer_calls = []
    original = service.storage.acquisitions()
    before = sqlite_source_stamp(database)
    assert before is not None

    def mutate_between_snapshot_and_completion(*args, **kwargs):
        audit = real_audit_library(*args, **kwargs)
        fixture_writer_calls.append(True)
        # Synthetic external writer simulates an independent source sync.
        # Production reconciliation never writes these tables.
        with sqlite3.connect(database) as db:
            db.execute(
                "INSERT INTO entitlements (entitlement_id,identity_id,state,provider_id)"
                " VALUES (?,?,?,?)",
                ("PRIVATE-new-right", "alice", "active", "numilog"),
            )
        return audit

    with patch.object(broker, "audit_library", side_effect=mutate_between_snapshot_and_completion) as spy:
        partial = broker.LibraryReconciliationReadBrokerImpl(service).audit(
            identity_id="alice",
        )
    assert spy.call_count == 1 and fixture_writer_calls == [True]
    assert sqlite_source_stamp(database) != before
    assert partial["status"] == "incomplete_scan"
    assert partial["reasons"] == ["snapshot_changed"]
    assert partial["read_only"] is True
    assert partial["acquisition_authorized"] is False
    assert partial["repair_authorized"] is False
    assert "reconciliation" not in partial
    assert service.storage.acquisitions() == original
    for forbidden in ("PRIVATE", str(database), str(root)):
        assert forbidden not in json.dumps(partial)

    # The next run takes a NEW coherent SQLite image while reusing only the
    # previously verified file observations. No stale account/right cache.
    fresh = broker.LibraryReconciliationReadBrokerImpl(service).audit(
        identity_id="alice",
    )
    assert fresh["status"] == "complete"
    assert fresh["inventory"]["reused_files"] == 1
    assert fresh["reconciliation"]["counts"]["rights_without_verified_file"] == 1
    assert "PRIVATE" not in json.dumps(fresh)


def test_read_only_audits_never_modify_source_sqlite_when_unchanged(scoped):
    service, database, _root = scoped
    start = sqlite_source_stamp(database)
    result = broker.LibraryReconciliationReadBrokerImpl(service).audit(
        identity_id="alice",
    )
    finish = sqlite_source_stamp(database)
    assert start == finish
    assert result["status"] == "complete"
    assert result["inventory"]["files"] == 1


def test_commit_between_snapshot_read_and_return_is_refused_before_inventory(scoped):
    """Close the post-snapshot/pre-stamp race without inventing fresh rights."""
    service, database, root = scoped
    read = broker.read_library_snapshot
    calls = []

    def read_then_concurrent_commit(*args, **kwargs):
        old_image = read(*args, **kwargs)
        calls.append("snapshot")
        # Happens after the source reader returned coherent *old* bytes,
        # before the calling broker gets control to start the inventory.
        with sqlite3.connect(database) as db:
            db.execute(
                "INSERT INTO entitlements (entitlement_id,identity_id,state,provider_id)"
                " VALUES (?,?,?,?)",
                ("PRIVATE-race-right", "alice", "active", "numilog"),
            )
        return old_image

    with patch.object(broker, "read_library_snapshot",
                      side_effect=read_then_concurrent_commit), patch.object(
        broker, "audit_library",
        side_effect=AssertionError("stale source image must not start scan"),
    ) as scanned:
        refused = broker.LibraryReconciliationReadBrokerImpl(service).audit(
            identity_id="alice",
        )
    scanned.assert_not_called()
    assert calls == ["snapshot"]
    assert refused["status"] == "incomplete_scan"
    assert refused["reasons"] == ["snapshot_changed"]
    assert "reconciliation" not in refused
    assert "PRIVATE" not in json.dumps(refused)
    # A subsequent clean attempt must read the new rights, never reusing
    # the snapshot captured before the synthetic concurrent source commit.
    accepted = broker.LibraryReconciliationReadBrokerImpl(service).audit(
        identity_id="alice",
    )
    assert accepted["status"] == "complete"
    assert accepted["reconciliation"]["counts"]["rights_without_verified_file"] == 1
