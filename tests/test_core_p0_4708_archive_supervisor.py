"""#4708: private one-shot supervisor; isolated synthetic SQL only."""
from pathlib import Path
from types import SimpleNamespace

from cifereca.core import missing_placement_archive_supervisor as supervisor
from cifereca.core import missing_placement_archive_sql as archive
from tests.test_core_p0_4708_archive_sql import rig
from tests.test_core_p0_4708_archive_admission import _observe


def _command(rig, monkeypatch, tmp_path, *, restore=False, **changes):
    store, db, row, args = rig
    service = SimpleNamespace(config=args["config"], storage=store,
                              current_identity=SimpleNamespace(identity_id=row[3]))
    lock = tmp_path / "archive.lock"
    lock.touch(exist_ok=True)
    lock.chmod(0o600)
    monkeypatch.setattr(supervisor, "acquisition_lock_file", lambda: lock)
    phase = "archived" if restore else "live"
    db.execute("BEGIN")
    try:
        witness = _observe(db, row, args, phase)
    finally:
        db.rollback()
    assert witness is not None
    options = {k: args[k] for k in (
        "expected_payload", "expected_digest", "expected_epoch", "max_records", "max_bytes",
    )}
    options.update(expected_review=witness, identity_id=row[3],
                   library_root=Path(row[2]).parent.parent,
                   restore=restore, confirmed=True, scope_current=lambda: True)
    options.update(changes)
    return service, supervisor._confirmed_archive_command(service, **options)


def test_supervisor_roundtrip_one_shot(rig, monkeypatch, tmp_path):
    store, db, row, args = rig
    _, run = _command(rig, monkeypatch, tmp_path)
    assert callable(run) and run() == "archived"
    assert run() == "confirmation_consumed"
    assert archive._live_row(db, row[0]) is None and not db.in_transaction
    _, restore = _command(rig, monkeypatch, tmp_path, restore=True)
    assert callable(restore) and restore() == "restored"
    assert restore() == "confirmation_consumed"
    assert archive._live_row(db, row[0]) == row


def test_supervisor_revoked_config_or_identity_never_mutates(rig, monkeypatch, tmp_path):
    store, db, row, args = rig
    service, run = _command(rig, monkeypatch, tmp_path)
    service.current_identity = SimpleNamespace(identity_id=row[3])
    assert run() == "blocked"
    assert archive._live_row(db, row[0]) == row
    service, run = _command(rig, monkeypatch, tmp_path)
    service.config.set("maintenance.destructive_cleanup_enabled", False)
    assert run() == "blocked"
    assert archive._live_row(db, row[0]) == row


def test_supervisor_postcheck_refusal_rolls_back(rig, monkeypatch, tmp_path):
    store, db, row, args = rig
    _, run = _command(rig, monkeypatch, tmp_path)
    old = supervisor._observe_archive_admission

    def changed(*a, **kw):
        if kw["phase"] == "archived":
            return None
        return old(*a, **kw)

    monkeypatch.setattr(supervisor, "_observe_archive_admission", changed)
    assert run() == "blocked"
    assert not db.in_transaction
    assert archive._live_row(db, row[0]) == row
    assert db.execute("SELECT count(*) FROM " + archive._TABLE).fetchone() == (0,)


def test_supervisor_postcommit_unverified_never_retries(rig, monkeypatch, tmp_path):
    store, db, row, args = rig
    _, run = _command(rig, monkeypatch, tmp_path)
    monkeypatch.setattr(supervisor, "review_archive_transition",
                        lambda *a, **kw: {"status": "unavailable"})
    assert run() == "commit_unverified"
    assert run() == "confirmation_consumed"
    assert archive._live_row(db, row[0]) is None
    assert db.execute("SELECT count(*) FROM " + archive._TABLE).fetchone() == (1,)


def test_supervisor_no_confirmation_and_nested_transaction(rig, monkeypatch, tmp_path):
    store, db, row, args = rig
    _, no = _command(rig, monkeypatch, tmp_path, confirmed=False)
    assert no is None
    _, run = _command(rig, monkeypatch, tmp_path)
    db.execute("BEGIN IMMEDIATE")
    try:
        assert run() == "blocked"
        assert db.in_transaction and archive._live_row(db, row[0]) == row
    finally:
        db.rollback()
