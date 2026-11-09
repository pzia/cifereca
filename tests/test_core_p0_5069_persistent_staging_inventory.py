"""P0 #5069 B7: known updater staging is inventory-only, never a purge."""
from __future__ import annotations

import json

from cifereca import updater
from cifereca.updater_retention_staging import inspect_persistent_staging


NOW = 2_000_000_000.0


def test_missing_stores_are_not_created(tmp_path):
    runtime, packages, core = (
        tmp_path / "runtime", tmp_path / "packages", tmp_path / "state" / "update-backups",
    )
    report = inspect_persistent_staging(runtime, packages, core_backups_root=core, now=NOW)
    assert report["plan"]["total"] == 0
    assert report["safe_to_apply"] is False
    assert not any(path.exists() for path in (runtime, packages, core))


def test_transaction_backup_and_plugin_staging_inventoried_without_payload_read(tmp_path):
    runtime = tmp_path / "runtime"
    packages = tmp_path / "packages"
    core = tmp_path / "core-backups"
    tx = runtime / "transactions" / ".20261108T101214Z-2b88f133f09d.123.tmp"
    tx.mkdir(parents=True)
    (tx / "transaction.json").write_text("PRIVATE_TRANSACTION_TOKEN")
    stage = packages / "org.private.account" / ".1.2.3-abcdefgh"
    stage.mkdir(parents=True)
    (stage / "private.bin").write_text("PRIVATE_PLUGIN_TOKEN")
    backup = core / ".20261108T101214Z-2b88f133f0.456.tmp"
    backup.mkdir(parents=True)
    (backup / "library.sqlite3").write_text("PRIVATE_SQLITE_TOKEN")
    before = [entry.stat().st_mtime_ns for entry in (tx, stage, backup)]
    report = inspect_persistent_staging(runtime, packages, core_backups_root=core, now=NOW)
    text = json.dumps(report)
    assert report["plan"]["total"] == 3
    assert report["plan"]["candidates"] == 0
    assert report["safe_to_apply"] is False
    assert "persistent_staging_unqualified" in report["blockers"]
    assert "core_transaction_staging" in report["coverage"]
    assert "core_backup_staging" in report["coverage"]
    assert "plugin_install_staging" in report["coverage"]
    assert all(row["decision"] == "retain" for row in report["plan"]["objects"])
    assert not any(secret in text for secret in (
        "PRIVATE_TRANSACTION_TOKEN", "PRIVATE_PLUGIN_TOKEN", "PRIVATE_SQLITE_TOKEN",
        "org.private.account", str(tmp_path),
    ))
    assert before == [entry.stat().st_mtime_ns for entry in (tx, stage, backup)]


def test_seven_undated_staging_generations_never_become_delete_candidates(tmp_path):
    runtime = tmp_path / "runtime"
    for i in range(7):
        (runtime / "transactions" / f".20261108T101214Z-2b88f133f09d.{i+1}.tmp").mkdir(parents=True)
    report = inspect_persistent_staging(runtime, tmp_path / "plugins", now=NOW)
    assert report["plan"]["total"] == 7
    assert report["plan"]["candidates"] == 0
    assert all("unknown_timestamp" in row["reasons"] for row in report["plan"]["objects"])
    assert report["safe_to_apply"] is False


def test_unknown_hidden_entry_and_external_symlink_never_followed(tmp_path):
    root = tmp_path / "runtime"
    folder = root / "transactions"
    folder.mkdir(parents=True)
    (folder / ".unknown-hidden").mkdir()
    outside = tmp_path / "credentials"
    outside.write_text("VERY_PRIVATE")
    (folder / ".20261108T101214Z-2b88f133f09d.5.tmp").symlink_to(outside)
    report = inspect_persistent_staging(root, tmp_path / "plugins", now=NOW)
    assert "unknown_hidden_staging" in report["blockers"]
    assert "unsafe_staging_entry" in report["blockers"]
    assert report["plan"]["total"] == 0
    assert "VERY_PRIVATE" not in json.dumps(report)
    link = tmp_path / "runtime-link"
    link.symlink_to(root, target_is_directory=True)
    refused = inspect_persistent_staging(link, tmp_path / "plugins", now=NOW)
    assert "unsafe_staging_store" in refused["blockers"]
    assert refused["plan"]["candidates"] == 0


def test_official_retention_cli_exposes_staging_plan_without_purge(tmp_path, monkeypatch, capsys):
    runtime = tmp_path / "runtime"
    stage = runtime / "transactions" / ".20261108T101214Z-2b88f133f09d.9.tmp"
    stage.mkdir(parents=True)
    monkeypatch.setattr(updater, "install_root", lambda: runtime)
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: tmp_path / "packages")
    monkeypatch.setattr(updater, "state_dir", lambda **_kw: tmp_path / "state")
    assert updater.main(["--retention-inspect", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["plans"]["known_persistent_staging"]["total"] == 1
    assert result["plans"]["known_persistent_staging"]["candidates"] == 0
    assert result["safe_to_apply"] is False
    # Still pending: the cross-store graph and ownership proofs are incomplete.
    assert "persistent_staging" in result["pending_stores"]
    assert stage.exists()
