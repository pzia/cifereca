"""P0 #2661 — local readiness is not first provider sync, and GTK stays async."""
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS

from cifereca.core.library_reconciliation_readiness import (
    ReconciliationReadiness, local_reconciliation_readiness,
)
from tests.test_library_audit_broker import scoped
from tests.test_library_reconciliation import filesystem
from tests.test_maintenance_cache_row_callbacks import mounted


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


def mount_ready_gate(scoped):
    service, _, _ = scoped
    m = mounted("library.reconciliation.audit")
    m.host.service = service
    m.refresh()
    row = m.host._tools_box.children[0].children[0]
    run = row.children[0]
    return m, service, row, run


def test_reconciliation_gate_checks_on_worker_and_enables_without_sync(scoped):
    m, service, row, run = mount_ready_gate(scoped)
    assert run.sensitive is False
    assert "Vérification" in row.subtitle
    args, kwargs = m.jobs[-1]
    assert kwargs["kind"] == "maintenance-plan"
    ready = args[1]()
    assert isinstance(ready, ReconciliationReadiness)
    args[2](ready)
    assert run.sensitive is True
    assert "hors réseau" in row.subtitle
    run.click()
    preview_args, kw = m.jobs[-1]
    assert kw["kind"] == "maintenance-plan"
    # Change the same SQLite source after the enablement snapshot:
    service.storage.path = Path("/not/created/library.sqlite")
    try:
        preview_args[1]()
        assert False, "worker must refuse stale local readiness"
    except RuntimeError as exc:
        assert str(exc) == "Préparation de la réconciliation impossible."
        assert exc.__suppress_context__


def test_unready_gate_is_visible_and_does_not_plan_or_poll_on_gtk(scoped, tmp_path):
    m, service, row, run = mount_ready_gate(scoped)
    service.storage.path = tmp_path / "not-yet-ready.sqlite"
    args, _kwargs = m.jobs[-1]
    args[2](args[1]())
    assert not run.sensitive
    assert "Initialisation" in row.subtitle
    before = len(m.jobs)
    run.click()  # a synthetic click must still fail closed
    assert len(m.jobs) == before
    assert m.tool.plans == 0


def test_superseded_readiness_callbacks_cannot_reenable_stale_tools(scoped):
    m, _service, old_row, old_run = mount_ready_gate(scoped)
    old_args, _kwargs = m.jobs[-1]
    m.refresh()
    new_row = m.host._tools_box.children[0].children[0]
    new_run = new_row.children[0]
    old_args[2](ReconciliationReadiness("ready_offline", "local_ready"))
    assert new_run.sensitive is False
    assert "Vérification" in new_row.subtitle
    args, _kwargs = m.jobs[-1]
    args[2](args[1]())
    assert new_run.sensitive
    assert not old_run.sensitive
