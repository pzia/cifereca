"""P0 #5069 B2: read-only coverage and conservative physical-history projection."""
from __future__ import annotations

import json
import os
from pathlib import Path

from cifereca import updater
from cifereca.updater_retention_inventory import inspect_core_transaction_history


NOW = 1_000_000.0


def _release(root: Path, name: str, installed_at: float):
    path = root / "releases" / name
    path.mkdir(parents=True)
    (path / ".cifereca-install.json").write_text('{"version":"1.0"}\n', encoding="utf-8")
    os.utime(path, (installed_at, installed_at))
    return path


def test_seven_recent_releases_are_retained_without_any_mutation(tmp_path):
    root = tmp_path / "runtime"
    releases = [_release(root, f"v{i}", NOW - i * 100) for i in range(7)]
    updater._replace_symlink(root / "current", releases[0])
    before = sorted(str(p) for p in root.rglob("*"))
    report = inspect_core_transaction_history(root, now=NOW)
    assert report["safe_to_apply"] is False
    assert "plugin_versions" in report["pending_stores"]
    assert report["plan"]["total"] == 7
    assert report["plan"]["candidates"] == 0
    assert all(row["decision"] == "retain" for row in report["plan"]["objects"])
    assert before == sorted(str(p) for p in root.rglob("*"))


def test_old_releases_plan_keeps_two_but_never_deletes(tmp_path):
    root = tmp_path / "runtime"
    for i in range(7):
        _release(root, f"v{i}", NOW - 100_000 - 100 * i)
    plan = inspect_core_transaction_history(root, now=NOW)["plan"]
    assert plan["total"] == 7
    assert plan["candidates"] == 5
    assert all((root / "releases" / f"v{i}").is_dir() for i in range(7))
    assert plan["safe_to_apply"] is False


def test_core_active_link_remains_pinned_when_very_old(tmp_path):
    root = tmp_path / "runtime"
    releases = [_release(root, f"v{i}", NOW - 100_000 - i * 100) for i in range(5)]
    updater._replace_symlink(root / "current", releases[4])
    report = inspect_core_transaction_history(root, now=NOW)
    assert report["plan"]["candidates"] == 2
    assert sum("active" in row["reasons"] for row in report["plan"]["objects"]) == 1


def test_missing_previous_reference_blocks_all_candidates(tmp_path):
    root = tmp_path / "runtime"
    release = _release(root, "v-current", NOW - 200_000)
    for i in range(3):
        _release(root, f"other-{i}", NOW - 201_000 - i)
    txid = "20261108T101214Z-12345678abcd"
    path = root / "transactions" / txid
    path.mkdir(parents=True)
    tx = {
        "schema": updater.TRANSACTION_SCHEMA,
        "id": txid, "state": "restoration_incomplete",
        "created_at": "20261108T101214Z",
        "core": {
            "before_current": {"path": str(release)},
            "before_previous": {"path": str(root / "releases" / "lost")},
            "after": {"path": str(release)},
        },
        "packages": {},
    }
    updater._write_json_atomic(path / "transaction.json", tx)
    original = (path / "transaction.json").read_bytes()
    report = inspect_core_transaction_history(root, now=NOW)
    assert "unresolved_transaction_reference" in report["blockers"]
    assert "incomplete_reference_graph" in report["plan"]["blockers"]
    assert report["plan"]["candidates"] == 0
    assert original == (path / "transaction.json").read_bytes()


def test_symlinked_history_store_is_a_blocker_without_traversal(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret").write_text("stay private", encoding="utf-8")
    (root / "transactions").symlink_to(private, target_is_directory=True)
    report = inspect_core_transaction_history(root, now=NOW)
    assert "unsafe_transactions_root" in report["blockers"]
    assert "private" not in json.dumps(report)
    assert "secret" not in json.dumps(report)
    assert (private / "secret").read_text(encoding="utf-8") == "stay private"


def test_symlinked_release_entry_is_not_traversed(tmp_path):
    root = tmp_path / "runtime"
    _release(root, "real", NOW)
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret").write_text("secret", encoding="utf-8")
    (root / "releases" / "bad").symlink_to(private, target_is_directory=True)
    report = inspect_core_transaction_history(root, now=NOW)
    assert "unsafe_release_entry" in report["blockers"]
    assert report["plan"]["total"] == 1
    assert (private / "secret").read_text(encoding="utf-8") == "secret"


def test_transaction_snapshot_and_core_reference_are_pinned(tmp_path):
    root = tmp_path / "runtime"
    release = _release(root, "v1", NOW - 200_000)
    txid = "20261108T101214Z-12345678abcd"
    path = root / "transactions" / txid
    (path / "plugins").mkdir(parents=True)
    tx = {
        "schema": updater.TRANSACTION_SCHEMA,
        "id": txid, "state": "prepared",
        "created_at": "20261108T101214Z",
        "core": {
            "before_current": {"path": str(release)},
            "before_previous": None,
            "after": {"path": str(release)},
        },
        "packages": {},
    }
    updater._write_json_atomic(path / "transaction.json", tx)
    report = inspect_core_transaction_history(root, now=NOW)
    assert report["plan"]["total"] == 3
    assert report["plan"]["candidates"] == 0
    assert "transaction_snapshots" in report["coverage"]
    assert any("referenced" in obj["reasons"] for obj in report["plan"]["objects"])


def test_symlinked_transaction_journal_is_never_read(tmp_path):
    root = tmp_path / "runtime"
    txid = "20261108T101214Z-12345678abcd"
    folder = root / "transactions" / txid
    folder.mkdir(parents=True)
    outside = tmp_path / "credentials.json"
    outside.write_text('{"secret":"PRIVATE"}', encoding="utf-8")
    (folder / "transaction.json").symlink_to(outside)
    report = inspect_core_transaction_history(root, now=NOW)
    assert "unsafe_transaction_journal" in report["blockers"]
    assert "PRIVATE" not in json.dumps(report)
    assert outside.read_text(encoding="utf-8") == '{"secret":"PRIVATE"}'
