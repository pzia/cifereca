"""P0 #2661 — local readiness is not first provider sync, and GTK stays async."""
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS

from cifereca.core.library_reconciliation_readiness import (
    ReconciliationReadiness, local_reconciliation_readiness,
)
from tests.test_library_audit_broker import scoped
from tests.test_library_reconciliation import filesystem


def test_existing_local_evidence_is_ready_without_any_provider_sync(scoped, tmp_path):
    service, database, _ = scoped
    def no_network(*_args, **_kwargs):
        raise AssertionError("provider sync is never a prerequisite")
    service.connect_configured = no_network
    before = filesystem(tmp_path)
    report = local_reconciliation_readiness(service, "alice")
    assert report.status == "ready_offline"
    assert report.reason == "local_ready"
    assert report.enabled
    assert "alice" not in repr(report)
    assert filesystem(tmp_path) == before


def test_local_initialization_and_active_identity_are_real_blockers(scoped, tmp_path):
    service, database, _ = scoped
    original = service.storage.path
    service.storage.path = tmp_path / "not-created.sqlite"
    state = local_reconciliation_readiness(service, "alice")
    assert (state.status, state.reason, state.enabled) == (
        "initializing", "storage_initializing", False,
    )
    service.storage.path = original
    service.current_identity = None
    state = local_reconciliation_readiness(service, "alice")
    assert (state.status, state.reason) == ("blocked", "identity_not_active")
    assert not state.enabled
    assert local_reconciliation_readiness(service, None).reason == "identity_missing"


def test_migration_blocks_but_live_wal_unknown_does_not_strand_button(scoped, tmp_path):
    service, database, _ = scoped
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS metadata "
                   "(key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (
            "identity-directory-migration-v1", "PRIVATE_JOURNAL",
        ))
    before = filesystem(tmp_path)
    report = local_reconciliation_readiness(service, "alice")
    assert report.status == "temporarily_busy"
    assert report.reason == "migration_pending" and not report.enabled
    assert "PRIVATE" not in repr(report)
    assert filesystem(tmp_path) == before
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (
            "OTHER", "SYNTHETIC",
        ))
        db.commit()
        # The WAL may contain a different migration state: no optimistic
        # conclusion from the base image, and no connection side-effects.
        before_wal = filesystem(tmp_path)
        report = local_reconciliation_readiness(service, "alice")
        assert report.status == "indeterminate" and report.enabled
        assert report.reason == "migration_unchecked"
        assert filesystem(tmp_path) == before_wal


def test_invalid_or_symlinked_sqlite_cannot_be_presented_as_ready(scoped, tmp_path):
    service, database, _ = scoped
    link = tmp_path / "link.sqlite"
    link.symlink_to(database)
    service.storage.path = link
    report = local_reconciliation_readiness(service, "alice")
    assert report.status == "blocked"
    assert report.reason == "storage_invalid"
    service.storage.path = Path("relative.sqlite")
    report = local_reconciliation_readiness(service, "alice")
    assert report.reason == "storage_invalid"
