"""P0 #5069 B12: snapshot retention is per updater history, not per tx."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from cifereca import updater
from cifereca.updater_retention_inventory import inspect_core_transaction_history


NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)


def _history(root, *, n=7, young=False, unfinished=()):
    folders = []
    origin = NOW - (timedelta(hours=1) if young else timedelta(days=20))
    for index in range(n):
        created = origin + timedelta(minutes=index)
        stamp = created.strftime("%Y%m%dT%H%M%SZ")
        txid = f"{stamp}-{index:012x}"
        folder = root / "transactions" / txid
        (folder / "plugins").mkdir(parents=True)
        (folder / "transaction.json").write_text(
            json.dumps({
                "schema": updater.TRANSACTION_SCHEMA,
                "id": txid,
                "state": "restoration_incomplete" if index in unfinished else "applied",
                "created_at": stamp,
                "core": {"before_current": None, "before_previous": None, "after": None},
                "packages": {},
            }),
            encoding="utf-8",
        )
        folders.append(folder)
    return folders


def _snapshot_decisions(report):
    return [row for row in report["plan"]["objects"]
            if row["object_id"].startswith("snapshot:")]


def _transaction_decisions(report):
    return [row for row in report["plan"]["objects"]
            if row["object_id"].startswith("transaction:")]


def test_seven_old_snapshots_have_one_shared_minimum_two_generation_floor(tmp_path):
    root = tmp_path / "runtime"
    folders = _history(root)
    before = sorted(str(item) for item in root.rglob("*"))
    report = inspect_core_transaction_history(root, now=NOW.timestamp())
    snapshots = _snapshot_decisions(report)
    txs = _transaction_decisions(report)
    assert len(snapshots) == len(txs) == 7
    assert sum(row["decision"] == "candidate" for row in snapshots) == 5
    assert sum(row["decision"] == "candidate" for row in txs) == 5
    assert report["plan"]["candidates"] == 10
    assert report["safe_to_apply"] is False
    assert all(folder.is_dir() and (folder / "plugins").is_dir()
               for folder in folders)
    assert sorted(str(item) for item in root.rglob("*")) == before


def test_seven_young_snapshots_all_remain_within_24h(tmp_path):
    root = tmp_path / "runtime"
    _history(root, young=True)
    report = inspect_core_transaction_history(root, now=NOW.timestamp())
    assert len(_snapshot_decisions(report)) == 7
    assert report["plan"]["candidates"] == 0
    assert all("within_24h" in row["reasons"]
               for row in _snapshot_decisions(report))
    assert report["safe_to_apply"] is False


def test_unfinished_old_transaction_pins_its_own_snapshot_beyond_floor(tmp_path):
    root = tmp_path / "runtime"
    _history(root, unfinished=(0,))
    report = inspect_core_transaction_history(root, now=NOW.timestamp())
    assert sum(row["decision"] == "retain"
               for row in _transaction_decisions(report)) == 3
    assert sum(row["decision"] == "retain"
               for row in _snapshot_decisions(report)) == 3
    assert sum("referenced" in row["reasons"] for row in
               _snapshot_decisions(report)) >= 1
    assert report["plan"]["candidates"] == 8
    assert report["safe_to_apply"] is False


def test_missing_snapshot_qualifies_no_independent_deletion(tmp_path):
    root = tmp_path / "runtime"
    folders = _history(root)
    for folder in folders:
        (folder / "plugins").rmdir()
    report = inspect_core_transaction_history(root, now=NOW.timestamp())
    assert len(_snapshot_decisions(report)) == 0
    assert len(_transaction_decisions(report)) == 7
    assert report["plan"]["candidates"] == 5
    assert report["safe_to_apply"] is False
