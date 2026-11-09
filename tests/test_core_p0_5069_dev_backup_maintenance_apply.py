"""P0 #5069: owner-qualified DevUpdater backup retention with real deletion."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os

import pytest

from cifereca import updater
from cifereca.updater_dev_backup_maintenance import dev_backup_maintenance


NOW = 2_000_000_000.0


def _backup(backups, i, *, age):
    name = f"{i:016x}-{1_000_000_000_000 + i}"
    folder = backups / name
    folder.mkdir(parents=True, mode=0o700)
    (folder / "plugins").mkdir()
    receipt = folder / ".cifereca-updater-owned.json"
    receipt.write_text(json.dumps({
        "schema": "cifereca.updater.dev-backup-receipt/1",
        "created_at": datetime.fromtimestamp(
            NOW - age, timezone.utc,
        ).isoformat(),
        "snapshot_sha256": "a" * 64,
        "package_count": 0,
        "backed_up_packages_sha256": sha256(b"").hexdigest(),
    }))
    receipt.chmod(0o600)
    return folder


def _fixture(tmp_path, *, age):
    runtime = tmp_path / "runtime"
    backups = tmp_path / "state" / "dev-updates"
    return runtime, backups, [_backup(backups, i, age=age + i * 60)
                              for i in range(7)]


def _lease(monkeypatch):
    monkeypatch.setattr(updater, "_trusted_package_lease",
                        lambda: __import__("contextlib").nullcontext())


def test_seven_recent_dev_backups_all_preserved(tmp_path):
    runtime, backups, folders = _fixture(tmp_path, age=300)
    plan = dev_backup_maintenance(runtime, backups, now=NOW)
    assert plan["candidate_backups"] == 0
    assert plan["safe_to_apply"] is False
    assert all(folder.is_dir() for folder in folders)


def test_seven_old_dev_backups_keep_two(tmp_path, monkeypatch):
    runtime, backups, folders = _fixture(tmp_path, age=3 * 86400)
    _lease(monkeypatch)
    plan = dev_backup_maintenance(runtime, backups, now=NOW)
    assert plan["candidate_backups"] == 5
    outcome = dev_backup_maintenance(
        runtime, backups, apply=True, now=NOW,
        acknowledge=plan["acknowledgement_sha256"],
    )
    assert outcome["removed"] == 5
    assert all(folder.is_dir() for folder in folders[:2])
    assert all(not folder.exists() for folder in folders[2:])
    assert len(list((backups.parent / ".cifereca-dev-backup-gc").glob("*.json"))) == 5


def test_manual_backup_prevents_any_deletion(tmp_path):
    runtime, backups, folders = _fixture(tmp_path, age=3 * 86400)
    manual = backups / "my-personal-backup"
    manual.mkdir()
    (manual / "notes.txt").write_text("private")
    with pytest.raises(ValueError, match="inventory_unqualified"):
        dev_backup_maintenance(runtime, backups, now=NOW)
    assert all(folder.is_dir() for folder in folders)
    assert (manual / "notes.txt").read_text() == "private"


def test_open_transaction_blocks_backup_gc(tmp_path):
    runtime, backups, folders = _fixture(tmp_path, age=3 * 86400)
    transaction = runtime / "transactions" / "unfinished"
    transaction.mkdir(parents=True)
    (transaction / "transaction.json").write_text(json.dumps({
        "schema": updater.TRANSACTION_SCHEMA,
        "id": "unfinished",
        "state": "restoration_incomplete",
        "created_at": "20261109T000000Z",
        "core": {}, "packages": {},
    }))
    with pytest.raises(ValueError, match="transaction_pending"):
        dev_backup_maintenance(runtime, backups, now=NOW)
    assert all(folder.is_dir() for folder in folders)


def test_wrong_acknowledgement_never_deletes_backups(tmp_path, monkeypatch):
    runtime, backups, folders = _fixture(tmp_path, age=3 * 86400)
    _lease(monkeypatch)
    with pytest.raises(ValueError, match="plan_changed"):
        dev_backup_maintenance(
            runtime, backups, apply=True, now=NOW,
            acknowledge="0" * 64,
        )
    assert all(folder.is_dir() for folder in folders)
    assert not (backups.parent / ".cifereca-dev-backup-gc").exists()


def test_plan_does_not_create_state_directory(tmp_path):
    runtime = tmp_path / "runtime"
    backups = tmp_path / "state" / "dev-updates"
    plan = dev_backup_maintenance(runtime, backups, now=NOW)
    assert plan["candidate_backups"] == 0
    assert not runtime.exists()
    assert not backups.parent.exists()
