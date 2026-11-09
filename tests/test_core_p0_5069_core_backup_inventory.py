"""P0 #5069 B5: no-delete inventory of Core pre-activation backup receipts."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from cifereca.updater_retention_core_backups import inspect_core_preactivation_backups


NOW = 2_000_000_000.0
PRIVATE = "opaque-private-label-and-credential"


def _backup(root, age: int, *, label: str = PRIVATE):
    stamp = datetime.fromtimestamp(NOW-age, tz=timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ",
    )
    key = sha256(label.encode("utf-8")).hexdigest()[:10]
    folder = root / (stamp+"-"+key)
    folder.mkdir(parents=True)
    (folder / "backup.json").write_text(
        json.dumps({"label": label, "created_at": stamp}),
        encoding="utf-8",
    )
    return folder


def test_missing_store_read_only(tmp_path):
    root = tmp_path / "update-backups"
    report = inspect_core_preactivation_backups(root, now=NOW)
    assert report["safe_to_apply"] is False
    assert report["plan"]["total"] == 0
    assert not root.exists()
    assert "core_backup_dependencies_unqualified" in report["blockers"]


def test_seven_backups_within_24h_all_retained(tmp_path):
    for age in range(1, 8):
        _backup(tmp_path, age * 3600)
    report = inspect_core_preactivation_backups(tmp_path, now=NOW)
    assert report["plan"]["total"] == 7
    assert report["plan"]["candidates"] == 0
    assert report["safe_to_apply"] is False


def test_minimum_two_after_window_with_theoretical_candidates(tmp_path):
    for age in (10, 9, 8, 7, 6, 5):
        _backup(tmp_path, age * 86400)
    report = inspect_core_preactivation_backups(tmp_path, now=NOW)
    assert report["plan"]["total"] == 6
    assert report["plan"]["candidates"] == 4
    assert sum("minimum_generations" in row["reasons"]
               for row in report["plan"]["objects"]) == 2
    assert report["safe_to_apply"] is False


def test_exact_24h_threshold_preserves_minimum(tmp_path):
    for age in (86400, 3*86400, 4*86400):
        _backup(tmp_path, age)
    report = inspect_core_preactivation_backups(tmp_path, now=NOW)
    assert report["plan"]["candidates"] == 1
    assert report["safe_to_apply"] is False


def test_tampered_label_refuses_ownership(tmp_path):
    folder = _backup(tmp_path, 5*86400)
    marker = folder / "backup.json"
    row = json.loads(marker.read_text(encoding="utf-8"))
    row["label"] = "modified"
    marker.write_text(json.dumps(row), encoding="utf-8")
    report = inspect_core_preactivation_backups(tmp_path, now=NOW)
    assert "core_backup_ownership_unqualified" in report["blockers"]
    assert "unowned" in report["plan"]["objects"][0]["reasons"]
    assert report["plan"]["candidates"] == 0


def test_malformed_journal_retained(tmp_path):
    folder = _backup(tmp_path, 5*86400)
    (folder / "backup.json").write_text("{", encoding="utf-8")
    report = inspect_core_preactivation_backups(tmp_path, now=NOW)
    assert "core_backup_ownership_unqualified" in report["blockers"]
    assert report["safe_to_apply"] is False


def test_symlink_journal_never_read(tmp_path):
    folder = _backup(tmp_path, 5*86400)
    marker = folder / "backup.json"
    marker.unlink()
    outside = tmp_path.parent / (tmp_path.name+"-secret")
    outside.write_text('{"token":"MY_SECRET"}', encoding="utf-8")
    marker.symlink_to(outside)
    report = inspect_core_preactivation_backups(tmp_path, now=NOW)
    assert "core_backup_ownership_unqualified" in report["blockers"]
    assert "MY_SECRET" not in json.dumps(report)
    assert outside.read_text(encoding="utf-8") == '{"token":"MY_SECRET"}'


def test_external_root_symlink_rejected(tmp_path):
    root = tmp_path / "update-backups"
    root.mkdir()
    shortcut = tmp_path / "alias"
    shortcut.symlink_to(root, target_is_directory=True)
    report = inspect_core_preactivation_backups(shortcut, now=NOW)
    assert report["blockers"] == ["unsafe_core_backup_root"]
    assert report["plan"] is None


def test_crashed_staging_never_treated_as_qualified_backup(tmp_path):
    _backup(tmp_path, 30*86400)
    (tmp_path / ".20000101T000000Z-deadbeef01.123.tmp").mkdir()
    report = inspect_core_preactivation_backups(tmp_path, now=NOW)
    assert "core_backup_staging_unqualified" in report["blockers"]
    assert report["safe_to_apply"] is False


def test_user_label_and_payload_never_leave_inventory(tmp_path):
    folder = _backup(tmp_path, 30*86400)
    (folder / "library.sqlite3").write_bytes(b"PRIVATE_DB_CONTENTS")
    (folder / "config").mkdir()
    report = inspect_core_preactivation_backups(tmp_path, now=NOW)
    serialized = json.dumps(report)
    assert PRIVATE not in serialized
    assert "PRIVATE_DB_CONTENTS" not in serialized
    assert str(tmp_path) not in serialized
    assert report["safe_to_apply"] is False
