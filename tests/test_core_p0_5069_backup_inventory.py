"""P0 #5069 B5: only updater-owned backup namespaces are inventoried."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from cifereca.updater_retention_backup_inventory import inspect_updater_backup_history


NOW = datetime(2026, 11, 8, 20, tzinfo=timezone.utc).timestamp()


def _backup(state, age, label, *, marker=True):
    stamp = datetime.fromtimestamp(NOW - age, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dirname = stamp + "-" + hashlib.sha256(label.encode()).hexdigest()[:10]
    path = state / "update-backups" / dirname
    path.mkdir(parents=True)
    if marker:
        (path / "backup.json").write_text(
            json.dumps({"label": label, "created_at": stamp}), encoding="utf-8",
        )
    (path / "library.sqlite3").write_bytes(b"PRIVATE-USER-DATA-NOT-READ")
    return path


def test_all_seven_recent_backups_retained_without_reading_private_payload(tmp_path):
    for i in range(7):
        _backup(tmp_path, 100 * i, f"commit-{i}")
    old = {p: p.read_bytes() for p in (tmp_path / "update-backups").rglob("library.sqlite3")}
    report = inspect_updater_backup_history(tmp_path, now=NOW)
    assert report["safe_to_apply"] is False
    assert report["plan"]["total"] == 7
    assert report["plan"]["candidates"] == 0
    assert "PRIVATE-USER-DATA" not in json.dumps(report)
    assert {p: p.read_bytes() for p in old} == old


def test_old_owned_backups_only_two_kept_by_policy_not_deleted(tmp_path):
    paths = [_backup(tmp_path, 100_000 + i * 120, f"sha-{i}") for i in range(7)]
    report = inspect_updater_backup_history(tmp_path, now=NOW)
    assert report["plan"]["candidates"] == 5
    assert sum(x["decision"] == "retain" for x in report["plan"]["objects"]) == 2
    assert all(p.is_dir() for p in paths)
    assert report["safe_to_apply"] is False


def test_unqualified_backup_is_always_conservatively_retained(tmp_path):
    path = _backup(tmp_path, 200_000, "original", marker=False)
    report = inspect_updater_backup_history(tmp_path, now=NOW)
    assert "updater_backup_ownership_unqualified" in report["blockers"]
    assert report["plan"]["objects"][0]["decision"] == "retain"
    assert "unowned" in report["plan"]["objects"][0]["reasons"]


def test_forged_marker_does_not_attest_ownership(tmp_path):
    path = _backup(tmp_path, 200_000, "original")
    (path / "backup.json").write_text(
        '{"created_at":"20261108T101214Z","label":"someone-else"}',
        encoding="utf-8",
    )
    report = inspect_updater_backup_history(tmp_path, now=NOW)
    assert "updater_backup_ownership_unqualified" in report["blockers"]
    assert report["plan"]["objects"][0]["decision"] == "retain"


def test_dev_backups_without_attested_receipts_never_candidates(tmp_path):
    root = tmp_path / "dev-updates"
    for i in range(7):
        (root / f"aaaa-{i}").mkdir(parents=True)
    report = inspect_updater_backup_history(tmp_path, now=NOW)
    assert "updater_backup_ownership_unqualified" in report["blockers"]
    assert report["plan"]["candidates"] == 0
    assert all(x["decision"] == "retain" for x in report["plan"]["objects"])


def test_manual_backups_outside_updater_namespaces_are_not_enumerated(tmp_path):
    (tmp_path / "manual-backups").mkdir()
    (tmp_path / "manual-backups" / "PRIVATE").write_bytes(b"secret")
    report = inspect_updater_backup_history(tmp_path, now=NOW)
    assert report["plan"]["total"] == 0
    assert "PRIVATE" not in json.dumps(report)
    assert (tmp_path / "manual-backups" / "PRIVATE").read_bytes() == b"secret"


def test_symlinked_backup_store_refused_without_traversal(tmp_path):
    private = tmp_path / "sensitive"
    private.mkdir()
    (private / "SECRET").write_text("token", encoding="utf-8")
    (tmp_path / "update-backups").symlink_to(private, target_is_directory=True)
    report = inspect_updater_backup_history(tmp_path, now=NOW)
    assert "unsafe_updater_backup_store" in report["blockers"]
    assert report["plan"]["total"] == 0
    assert "token" not in json.dumps(report)
    assert (private / "SECRET").read_text(encoding="utf-8") == "token"


def test_symlinked_backup_entry_is_never_read(tmp_path):
    private = tmp_path / "sensitive"
    private.mkdir()
    (private / "backup.json").write_text('{"label":"private"}', encoding="utf-8")
    store = tmp_path / "update-backups"
    store.mkdir()
    (store / "unsafe").symlink_to(private, target_is_directory=True)
    report = inspect_updater_backup_history(tmp_path, now=NOW)
    assert "unsafe_updater_backup_entry" in report["blockers"]
    assert report["plan"]["total"] == 0
    assert "private" not in json.dumps(report)


def test_symlinked_marker_cannot_claim_original_receipt(tmp_path):
    path = _backup(tmp_path, 200_000, "original")
    marker = path / "backup.json"
    marker.unlink()
    external = tmp_path / "external.json"
    external.write_text('{"label":"original","created_at":"20261108T000000Z"}',
                        encoding="utf-8")
    marker.symlink_to(external)
    report = inspect_updater_backup_history(tmp_path, now=NOW)
    assert "updater_backup_ownership_unqualified" in report["blockers"]
    assert report["plan"]["objects"][0]["decision"] == "retain"


def test_maintenance_overview_aggregates_only_explicit_updater_backup_namespace(tmp_path):
    from cifereca.updater_maintenance_inspect import inspect_maintenance

    for i in range(4):
        _backup(tmp_path / "state", 200_000 + i * 100, f"commit-{i}")
    report = inspect_maintenance(
        runtime_root=tmp_path / "missing-runtime",
        packages_root=tmp_path / "missing-packages",
        state_root=tmp_path / "state",
        now=NOW,
    )
    backup_rows = [item for item in report["stores"] if item["store"] == "backup"]
    assert len(backup_rows) == 1
    assert backup_rows[0]["observed"] == 4
    assert backup_rows[0]["retained"] == 2
    assert backup_rows[0]["candidates_unverified"] == 2
    assert report["safe_to_apply"] is False
    assert "updater_backups" in report["coverage"]
    assert "cross_store_reference_graph_not_attested" in report["blockers"]
    assert "commit-" not in json.dumps(report)


def test_state_dir_create_false_does_not_modify_home(tmp_path, monkeypatch):
    from cifereca.paths import state_dir

    fresh = tmp_path / "missing-state-home"
    monkeypatch.setenv("XDG_STATE_HOME", str(fresh))
    path = state_dir(create=False)
    assert path == fresh / "cifereca"
    assert not fresh.exists()
    assert state_dir() == path
    assert path.is_dir()


def test_maintenance_cli_does_not_initialize_missing_state_root(tmp_path, monkeypatch, capsys):
    from cifereca import updater_maintenance_inspect

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "missing-state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "missing-data"))
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(tmp_path / "missing-runtime"))
    assert updater_maintenance_inspect.main(["--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["safe_to_apply"] is False
    assert "updater_backups" in output["coverage"]
    assert not (tmp_path / "missing-state").exists()
    assert not (tmp_path / "missing-data").exists()
    assert not (tmp_path / "missing-runtime").exists()


def test_hardlinked_backup_receipt_cannot_attest_owned_backup(tmp_path):
    import os
    source = _backup(tmp_path, 200_000, "original")
    os.link(source / "backup.json", tmp_path / "another-hardlink")
    result = inspect_updater_backup_history(tmp_path, now=NOW)
    assert "updater_backup_ownership_unqualified" in result["blockers"]
    assert result["plan"]["objects"][0]["decision"] == "retain"
