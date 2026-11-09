"""P0 #5069: a finished transaction's 24-hour TTL starts at finalization."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from cifereca import updater
from cifereca.updater_retention_inventory import (
    _name_id, inspect_core_transaction_history,
)


NOW = 2_000_000_000.0


def _iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _tx(root, index, *, created, updated="absent", snapshot=False):
    name = f"tx-{index}"
    path = root / "transactions" / name
    path.mkdir(parents=True)
    tx = {
        "schema": updater.TRANSACTION_SCHEMA,
        "id": name, "created_at": _stamp(created), "state": "applied",
        "core": {"before_current": None, "before_previous": None, "after": None},
        "packages": {},
    }
    if updated != "absent":
        tx["updated_at"] = updated
    (path / "transaction.json").write_text(json.dumps(tx))
    if snapshot:
        (path / "plugins").mkdir()
    return path


def _row(plan, name):
    return next(row for row in plan["objects"]
                if row["object_id"] == _name_id("transaction", name))


def test_recent_finalization_retains_old_created_transaction_and_snapshot(tmp_path):
    root = tmp_path / "runtime"
    for i in range(7):
        _tx(root, i, created=NOW - 8 * 86400 - i * 100,
            updated=_iso(NOW - 1 * 86400 - 3600), snapshot=True)
    # Oldest transaction is the only one finalized recently.
    oldest = root / "transactions" / "tx-6" / "transaction.json"
    tx = json.loads(oldest.read_text())
    tx["updated_at"] = _iso(NOW - 60)
    oldest.write_text(json.dumps(tx))
    report = inspect_core_transaction_history(root, now=NOW)
    row = _row(report["plan"], "tx-6")
    assert row["decision"] == "retain"
    assert "within_24h" in row["reasons"]
    snapshot = next(r for r in report["plan"]["objects"]
                    if r["object_id"] == _name_id("snapshot", "tx-6"))
    assert snapshot["decision"] == "retain"
    assert "referenced" in snapshot["reasons"]
    assert report["safe_to_apply"] is False


@pytest.mark.parametrize("updated", [
    "invalid", False, "2026-11-09T12:00:00",
    _iso(NOW - 20 * 86400),
])
def test_invalid_or_precreation_finalization_keeps_transaction(tmp_path, updated):
    root = tmp_path / "runtime"
    _tx(root, 0, created=NOW - 8 * 86400, updated=updated)
    report = inspect_core_transaction_history(root, now=NOW)
    row = _row(report["plan"], "tx-0")
    assert row["decision"] == "retain"
    assert "unknown_timestamp" in row["reasons"]
    assert report["safe_to_apply"] is False


def test_seven_old_completed_transactions_still_keep_exactly_two(tmp_path):
    root = tmp_path / "runtime"
    for i in range(7):
        created = NOW - 8 * 86400 - i * 100
        _tx(root, i, created=created, updated=_iso(created + 60))
    report = inspect_core_transaction_history(root, now=NOW)
    assert report["plan"]["candidates"] == 5
    assert _row(report["plan"], "tx-0")["decision"] == "retain"
    assert _row(report["plan"], "tx-1")["decision"] == "retain"
    assert all(_row(report["plan"], f"tx-{i}")["decision"] == "candidate"
               for i in range(2, 7))


def test_finalization_exactly_twenty_four_hours_ago_is_not_young(tmp_path):
    root = tmp_path / "runtime"
    for i in range(7):
        _tx(root, i, created=NOW - 10 * 86400 - i * 100,
            updated=_iso(NOW - 86400 - i * 60))
    report = inspect_core_transaction_history(root, now=NOW)
    assert _row(report["plan"], "tx-0")["decision"] == "retain"
    assert "within_24h" not in _row(report["plan"], "tx-0")["reasons"]
    assert report["plan"]["candidates"] == 5


def test_legacy_transaction_without_updated_at_uses_creation_clock(tmp_path):
    root = tmp_path / "runtime"
    for i in range(7):
        _tx(root, i, created=NOW - 8 * 86400 - i * 100)
    report = inspect_core_transaction_history(root, now=NOW)
    assert report["plan"]["candidates"] == 5
    assert report["safe_to_apply"] is False
