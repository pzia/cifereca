"""P0 #5069 B6: CLI combines disjoint backup stores without purge authority."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from cifereca import updater
from cifereca.updater_retention_plugins import inspect_updater_retention_readonly


def _create_core_backup(state_root, label: str, stamp: str):
    key = sha256(label.encode("utf-8")).hexdigest()[:10]
    folder = state_root / "update-backups" / (stamp + "-" + key)
    folder.mkdir(parents=True)
    (folder / "backup.json").write_text(
        json.dumps({"label": label, "created_at": stamp}), encoding="utf-8",
    )
    (folder / "library.sqlite3").write_bytes(b"PRIVATE_DATABASE_CONTENT")
    return folder


def test_cli_combines_backups_without_reading_payload_or_exposing_label(
    tmp_path, monkeypatch, capsys,
):
    state_root = tmp_path / "state"
    label = "my-private-books-and-account"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = _create_core_backup(state_root, label, stamp)
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: tmp_path / "packages")
    monkeypatch.setattr(updater, "state_dir", lambda **_kw: state_root)
    before = (folder / "library.sqlite3").read_bytes()
    assert updater.main(["--retention-inspect", "--json"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert "core_preactivation_backups" in report["plans"]
    assert report["plans"]["core_preactivation_backups"]["total"] == 1
    assert report["plans"]["updater_backups"]["total"] == 0
    assert report["safe_to_apply"] is False
    assert label not in output
    assert "PRIVATE_DATABASE_CONTENT" not in output
    assert str(tmp_path) not in output
    assert (folder / "library.sqlite3").read_bytes() == before


def test_aggregate_marks_unqualified_core_staging_without_authorizing_gc(tmp_path):
    runtime = tmp_path / "runtime"
    plugins = tmp_path / "packages"
    backups = tmp_path / "dev"
    core = tmp_path / "update-backups"
    core.mkdir()
    (core / ".partial.tmp").mkdir()
    report = inspect_updater_retention_readonly(
        runtime, plugins, now=2_000_000_000,
        backups_root=backups, core_backups_root=core,
    )
    assert report["plans"]["updater_backups"] is not None
    assert report["plans"]["core_preactivation_backups"] is not None
    assert report["safe_to_apply"] is False
    assert "core_backup_staging_unqualified" in report["blockers"]
    assert "cross_store_reference_proofs" in report["pending_stores"]


def test_absent_core_backup_root_keeps_plan_and_does_not_allocate(tmp_path):
    root = tmp_path / "not-created"
    report = inspect_updater_retention_readonly(
        tmp_path / "runtime", tmp_path / "packages", now=2_000_000_000,
        core_backups_root=root,
    )
    assert not root.exists()
    assert report["plans"]["core_preactivation_backups"]["total"] == 0
    assert report["safe_to_apply"] is False
