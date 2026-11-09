"""P0 #5069: release -> transaction -> snapshot is a complete rollback pin chain."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from cifereca import updater
from cifereca.updater_retention_inventory import (
    _name_id, inspect_core_transaction_history,
)


NOW = 2_000_000_000.0


def _release(root, name, age):
    release = root / "releases" / name
    release.mkdir(parents=True)
    receipt = {
        "version": name,
        "installed_at": datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(),
    }
    (release / ".cifereca-install.json").write_text(json.dumps(receipt))
    os.utime(release, (NOW - age, NOW - age))
    return release


def _transaction(root, name, *, age, snapshot=False):
    folder = root / "transactions" / name
    folder.mkdir(parents=True)
    stamp = datetime.fromtimestamp(NOW - age, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (folder / "transaction.json").write_text(json.dumps({
        "schema": updater.TRANSACTION_SCHEMA,
        "id": name, "created_at": stamp, "state": "applied",
        "core": {"before_current": None, "before_previous": None, "after": None},
        "packages": {},
    }))
    if snapshot:
        (folder / "plugins").mkdir()
    return folder


def test_active_release_pins_old_transaction_beyond_last_two(tmp_path):
    root = tmp_path / "runtime"
    release = _release(root, "current", age=10 * 86400)
    second = _release(root, "previous", age=11 * 86400)
    updater._replace_symlink(root / "current", release)
    updater._replace_symlink(root / "previous", second)
    for i in range(7):
        _transaction(root, f"tx-{i}", age=3 * 86400 + 100 * i, snapshot=True)
    (release / updater.TRANSACTION_FILE).write_text(
        json.dumps({"schema": 1, "id": "tx-6"}),
    )
    before = sorted(str(p) for p in root.rglob("*"))
    report = inspect_core_transaction_history(root, now=NOW)
    txid = _name_id("transaction", "tx-6")
    decisions = {r["object_id"]: r for r in report["plan"]["objects"]}
    assert decisions[txid]["decision"] == "retain"
    assert "referenced" in decisions[txid]["reasons"]
    snapshotid = _name_id("snapshot", "tx-6")
    assert decisions[snapshotid]["decision"] == "retain"
    assert "referenced" in decisions[snapshotid]["reasons"]
    assert report["safe_to_apply"] is False
    assert sorted(str(p) for p in root.rglob("*")) == before


def test_dangling_release_transaction_pointer_blocks_all_candidates(tmp_path):
    root = tmp_path / "runtime"
    release = _release(root, "current", age=3 * 86400)
    for i in range(4):
        _release(root, f"stale-{i}", age=(5 + i) * 86400)
    updater._replace_symlink(root / "current", release)
    (release / updater.TRANSACTION_FILE).write_text(
        json.dumps({"schema": 1, "id": "missing-transaction"}),
    )
    report = inspect_core_transaction_history(root, now=NOW)
    assert "incomplete_reference_graph" in report["plan"]["blockers"]
    assert report["plan"]["candidates"] == 0
    assert report["safe_to_apply"] is False


def test_release_transaction_pointer_symlink_never_follows_private_data(tmp_path):
    root = tmp_path / "runtime"
    release = _release(root, "current", age=3 * 86400)
    outside = tmp_path / "private.json"
    outside.write_text('{"secret":"PRIVATE"}')
    (release / updater.TRANSACTION_FILE).symlink_to(outside)
    report = inspect_core_transaction_history(root, now=NOW)
    assert "invalid_release_transaction_pointer" in report["blockers"]
    assert "incomplete_reference_graph" in report["plan"]["blockers"]
    assert report["plan"]["candidates"] == 0
    assert "PRIVATE" not in json.dumps(report)
    assert outside.read_text() == '{"secret":"PRIVATE"}'


def test_legacy_release_without_transaction_pointer_is_supported(tmp_path):
    root = tmp_path / "runtime"
    releases = [_release(root, f"v-{i}", age=(4+i)*86400) for i in range(7)]
    report = inspect_core_transaction_history(root, now=NOW)
    assert report["plan"]["candidates"] == 5
    assert "invalid_release_transaction_pointer" not in report["blockers"]
    assert all(path.is_dir() for path in releases)
