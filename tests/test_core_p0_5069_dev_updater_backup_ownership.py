"""P0 #5069 B4: durable DevUpdater backup receipts and safe inventory."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from cifereca.updater_retention_backups import (
    inspect_dev_updater_backups,
    write_dev_backup_receipt,
)


NOW = 2_000_000_000.0
PRIVATE = "org.example.private-account"


def _backup(root: Path, sequence: int, age: int, *, receipt: bool = True) -> Path:
    folder = root / ("a" * 16 + "-" + str(1_700_000_000_000_000_000 + sequence))
    folder.mkdir(parents=True, mode=0o700)
    plugin = folder / "plugins" / PRIVATE
    plugin.mkdir(parents=True)
    (plugin / "sample.bin").write_bytes(b"do not read or delete")
    if receipt:
        write_dev_backup_receipt(folder, snapshot_sha256="f"*64)
        marker = folder / ".cifereca-updater-owned.json"
        row = json.loads(marker.read_text(encoding="utf-8"))
        row["created_at"] = datetime.fromtimestamp(NOW-age, timezone.utc).isoformat()
        marker.write_text(json.dumps(row), encoding="utf-8")
        marker.chmod(0o600)
    return folder


def test_receipt_is_private_bound_to_actual_backup_directory(tmp_path):
    folder = _backup(tmp_path, 1, 3600)
    receipt = folder / ".cifereca-updater-owned.json"
    row = json.loads(receipt.read_text(encoding="utf-8"))
    assert row["schema"] == "cifereca.updater.dev-backup-receipt/1"
    assert row["package_count"] == 1
    assert len(row["backed_up_packages_sha256"]) == 64
    assert PRIVATE not in receipt.read_text(encoding="utf-8")
    assert receipt.stat().st_mode & 0o077 == 0
    assert folder.stat().st_mode & 0o077 == 0
    report = inspect_dev_updater_backups(tmp_path, now=NOW)
    assert report["coverage"] == ["updater_backups"]
    assert report["safe_to_apply"] is False
    assert report["plan"]["total"] == 1
    assert report["plan"]["candidates"] == 0


def test_receipt_never_overwritten_after_first_write(tmp_path):
    folder = _backup(tmp_path, 1, 90000)
    receipt = folder / ".cifereca-updater-owned.json"
    before = receipt.read_bytes()
    with pytest.raises(FileExistsError):
        write_dev_backup_receipt(folder, snapshot_sha256="f"*64)
    assert receipt.read_bytes() == before


def test_legacy_backup_without_receipt_is_unowned_and_pinned(tmp_path):
    _backup(tmp_path, 1, 40*86400, receipt=False)
    report = inspect_dev_updater_backups(tmp_path, now=NOW)
    assert report["safe_to_apply"] is False
    assert "backup_ownership_unqualified" in report["blockers"]
    assert report["plan"]["objects"][0]["decision"] == "retain"
    assert "unowned" in report["plan"]["objects"][0]["reasons"]


def test_24h_union_plus_minimum_two_generations_without_purge(tmp_path):
    for n in range(1, 7):
        _backup(tmp_path, n, (6-n)*86400)
    # Six backups; two newest required, four old theoretically eligible.
    report = inspect_dev_updater_backups(tmp_path, now=NOW)
    assert report["plan"]["total"] == 6
    assert report["plan"]["candidates"] == 4
    assert report["safe_to_apply"] is False
    assert "cross_store_backup_dependencies_unqualified" in report["blockers"]


def test_under_24h_never_candidate_even_with_many_backups(tmp_path):
    for n in range(1, 8):
        _backup(tmp_path, n, n*3600)
    report = inspect_dev_updater_backups(tmp_path, now=NOW)
    assert report["plan"]["candidates"] == 0


def test_tampered_backup_name_set_invalidates_ownership(tmp_path):
    folder = _backup(tmp_path, 1, 90000)
    (folder / "plugins" / "unexpected").mkdir()
    report = inspect_dev_updater_backups(tmp_path, now=NOW)
    assert "backup_ownership_unqualified" in report["blockers"]
    assert "unowned" in report["plan"]["objects"][0]["reasons"]


def test_unsafe_plugin_symlink_blocks_receipt(tmp_path):
    folder = tmp_path / ("b"*16 + "-1700000000000000000")
    folder.mkdir(mode=0o700)
    external = tmp_path / "external"
    external.mkdir()
    (folder / "plugins").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="backup_plugins_unsafe"):
        write_dev_backup_receipt(folder, snapshot_sha256="a"*64)
    assert not (folder / ".cifereca-updater-owned.json").exists()


def test_wrong_permissions_or_sha_refuse_ownership(tmp_path):
    folder = tmp_path / ("c"*16 + "-1700000000000000000")
    (folder / "plugins").mkdir(parents=True)
    folder.chmod(0o755)
    with pytest.raises(ValueError, match="backup_directory_permissions_unsafe"):
        write_dev_backup_receipt(folder, snapshot_sha256="a"*64)
    folder.chmod(0o700)
    with pytest.raises(ValueError, match="backup_snapshot_sha256_invalid"):
        write_dev_backup_receipt(folder, snapshot_sha256="not-a-sha")


def test_symlink_root_and_entry_never_followed(tmp_path):
    source = tmp_path / "original"
    source.mkdir()
    symlink = tmp_path / "alias"
    symlink.symlink_to(source, target_is_directory=True)
    assert inspect_dev_updater_backups(symlink, now=NOW)["blockers"] == ["unsafe_backups_root"]
    (source / "linked-backup").symlink_to(tmp_path, target_is_directory=True)
    report = inspect_dev_updater_backups(source, now=NOW)
    assert "unsafe_backup_entry" in report["blockers"]
    assert report["safe_to_apply"] is False


def test_redacted_ids_and_nonexistent_root_no_mutation(tmp_path):
    absent = tmp_path / "not-created"
    report = inspect_dev_updater_backups(absent, now=NOW)
    assert not absent.exists()
    assert report["safe_to_apply"] is False
    _backup(tmp_path, 1, 90000)
    report = inspect_dev_updater_backups(tmp_path, now=NOW)
    dumped = json.dumps(report)
    assert PRIVATE not in dumped
    assert str(tmp_path) not in dumped
