"""P0 #5069: physical retention for Core preactivation backups."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from cifereca import updater
from cifereca.updater_core_backup_maintenance import core_backup_maintenance

NOW = 2_000_000_000.0


def _backup(base, number, age):
    timestamp = datetime.fromtimestamp(
        NOW - age, timezone.utc,
    ).strftime("%Y%m%dT%H%M%SZ")
    label = f"core-install-{number}"
    folder = base / (timestamp + "-" + sha256(label.encode()).hexdigest()[:10])
    folder.mkdir(parents=True)
    (folder / "backup.json").write_text(json.dumps({
        "label": label, "created_at": timestamp,
    }))
    (folder / "database.sqlite3").write_bytes(b"PRIVATE BACKUP: fixture")
    return folder


def _fixture(tmp_path, *, age):
    runtime = tmp_path / "runtime"
    backups = tmp_path / "state" / "update-backups"
    folders = [_backup(backups, n, age + n * 60) for n in range(7)]
    return runtime, backups, folders


def _lease(monkeypatch):
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())


def test_seven_recent_core_backups_preserved(tmp_path):
    runtime, backups, folders = _fixture(tmp_path, age=120)
    plan = core_backup_maintenance(runtime, backups, now=NOW)
    assert plan["candidate_backups"] == 0
    assert all(folder.exists() for folder in folders)


def test_seven_old_core_backups_reduce_to_two(tmp_path, monkeypatch):
    runtime, backups, folders = _fixture(tmp_path, age=3 * 86400)
    _lease(monkeypatch)
    plan = core_backup_maintenance(runtime, backups, now=NOW)
    assert plan["candidate_backups"] == 5
    result = core_backup_maintenance(
        runtime, backups, now=NOW, apply=True,
        acknowledge=plan["acknowledgement_sha256"],
    )
    assert result["removed"] == 5
    assert all(folder.is_dir() for folder in folders[:2])
    assert all(not folder.exists() for folder in folders[2:])
    assert len(list((backups.parent / ".cifereca-core-backup-gc").glob("*.json"))) == 5


def test_manual_backup_blocks_destructive_gc(tmp_path):
    runtime, backups, folders = _fixture(tmp_path, age=3 * 86400)
    manual = backups / "manual-personal"
    manual.mkdir()
    (manual / "private.txt").write_text("KEEP")
    with pytest.raises(ValueError, match="inventory_unqualified"):
        core_backup_maintenance(runtime, backups, now=NOW)
    assert all(folder.is_dir() for folder in folders)
    assert (manual / "private.txt").read_text() == "KEEP"


def test_unfinished_restore_blocks_core_backup_deletion(tmp_path):
    runtime, backups, folders = _fixture(tmp_path, age=3 * 86400)
    tx = runtime / "transactions" / "unfinished"
    tx.mkdir(parents=True)
    (tx / "transaction.json").write_text(json.dumps({
        "schema": updater.TRANSACTION_SCHEMA,
        "id": "unfinished",
        "state": "restoration_incomplete",
        "created_at": "20261109T000000Z",
        "core": {}, "packages": {},
    }))
    with pytest.raises(ValueError, match="transaction_pending"):
        core_backup_maintenance(runtime, backups, now=NOW)
    assert all(folder.is_dir() for folder in folders)


def test_mismatched_acknowledgement_preserves_core_backups(tmp_path, monkeypatch):
    runtime, backups, folders = _fixture(tmp_path, age=3 * 86400)
    _lease(monkeypatch)
    with pytest.raises(ValueError, match="plan_changed"):
        core_backup_maintenance(
            runtime, backups, now=NOW, apply=True, acknowledge="0" * 64,
        )
    assert all(folder.is_dir() for folder in folders)
    assert not (backups.parent / ".cifereca-core-backup-gc").exists()


def test_missing_core_backup_root_is_plan_read_only(tmp_path):
    runtime, backups = tmp_path / "runtime", tmp_path / "state" / "update-backups"
    plan = core_backup_maintenance(runtime, backups, now=NOW)
    assert plan["candidate_backups"] == 0
    assert not backups.parent.exists()


def test_cli_core_backups_plan_does_not_create_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(updater, "state_dir", lambda create=False: tmp_path / "state")
    assert updater.main(["--maintenance-core-backups", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "plan" and result["candidate_backups"] == 0
    assert not (tmp_path / "state").exists()
