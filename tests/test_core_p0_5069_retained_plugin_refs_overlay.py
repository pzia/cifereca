"""P0 #5069 B10: retained Core transaction pins exact historical plugin bytes."""
from __future__ import annotations

from hashlib import sha256
import json

from cifereca import updater, updater_retention_plugins as combined
from cifereca.updater_retention import RetentionObject, plan_retention
from cifereca.updater_retention_cross_store import (
    _plugin_version_id, inspect_transaction_plugin_references,
)

PACKAGE = "org.synthetic.updater"
TX = "20261108T101214Z-2b88f133f09d"


def _plan(now=1_900_000_000):
    return plan_retention([
        RetentionObject(
            object_id=_plugin_version_id(PACKAGE, f"1.{i}"),
            store="plugin_versions", unit="synthetic-unit",
            generation=f"1.{i}", installed_at=now - 100_000 + i,
            owned_by_updater=True,
        ) for i in range(4)
    ], now=now)


def _tx(root):
    directory = root / "transactions" / TX
    directory.mkdir(parents=True)
    (directory / "transaction.json").write_text(json.dumps({
        "schema": updater.TRANSACTION_SCHEMA, "id": TX,
        "created_at": "20261108T101214Z", "state": "applied",
        "core": {}, "packages": {
            PACKAGE: {
                "before": {"exists": True, "versions": ["1.0"]},
                "after": {"exists": True, "versions": ["1.3"]},
            },
        },
    }))
    return directory


def _core_plan(now=1_900_000_000):
    return plan_retention([
        RetentionObject(
            object_id="transaction:" + sha256(TX.encode()).hexdigest(),
            store="transactions", unit="updater", generation=TX,
            installed_at=now - 90_000, owned_by_updater=True, pinned=True,
        )
    ], now=now)


def test_exact_historical_candidate_is_retained_by_qualified_transaction(tmp_path):
    root = tmp_path / "runtime"
    _tx(root)
    raw = _plan()
    old = _plugin_version_id(PACKAGE, "1.0")
    assert next(r for r in raw["objects"] if r["object_id"] == old)["decision"] == "candidate"
    refs = inspect_transaction_plugin_references(
        root, raw, core_transaction_plan=_core_plan(),
        _include_internal_resolved_refs=True,
    )
    assert refs["retention_edges_qualified"] is True
    assert refs["resolved_retained_edges"] == 2
    projected = combined._overlay_attested_transaction_pins(
        raw, refs["_resolved_retained_plugin_ids"],
    )
    assert projected is not raw and projected["safe_to_apply"] is False
    entry = next(r for r in projected["objects"] if r["object_id"] == old)
    assert entry["decision"] == "retain"
    assert entry["reasons"] == ["retained_transaction_reference"]
    assert projected["candidates"] == raw["candidates"] - 1
    assert next(r for r in raw["objects"] if r["object_id"] == old)["decision"] == "candidate"


def test_incomplete_or_unknown_pin_set_does_not_invent_gc_authority():
    original = _plan()
    for refs in (None, ["plugin_version:bad"], ["plugin_version:" + "f" * 64]):
        result = combined._overlay_attested_transaction_pins(original, refs)
        assert result is original
        assert result["safe_to_apply"] is False


def test_default_cross_store_report_never_emits_internal_reference_ids(tmp_path):
    root = tmp_path / "runtime"
    _tx(root)
    report = inspect_transaction_plugin_references(
        root, _plan(), core_transaction_plan=_core_plan(),
    )
    assert "_resolved_retained_plugin_ids" not in report
    assert PACKAGE not in json.dumps(report)
    assert report["safe_to_apply"] is False


def test_official_combined_inventory_uses_pins_but_does_not_publish_ids(
    tmp_path, monkeypatch,
):
    from cifereca import updater_retention_inventory, updater_retention_staging

    root = tmp_path / "runtime"
    _tx(root)
    initial = _plan()
    now = 1_900_000_000
    core_plan = _core_plan()
    monkeypatch.setattr(
        updater_retention_inventory, "inspect_core_transaction_history",
        lambda _root, *, now: {
            "coverage": ["transactions"], "blockers": [],
            "plan": core_plan,
        },
    )
    monkeypatch.setattr(
        combined, "inspect_plugin_version_history",
        lambda _root, *, now: {
            "coverage": ["plugin_versions"], "blockers": [],
            "plan": initial,
        },
    )
    monkeypatch.setattr(
        updater_retention_staging, "inspect_persistent_staging",
        lambda *_a, **_k: {
            "coverage": ["staging"], "blockers": [],
            "plan": plan_retention([], now=now),
        },
    )
    result = combined.inspect_updater_retention_readonly(
        root, tmp_path / "packages", now=now,
    )
    plan = result["plans"]["plugin_versions"]
    assert plan["candidates"] == initial["candidates"] - 1
    assert result["cross_store_reference_inspection"]["retention_edges_qualified"]
    assert "_resolved_retained_plugin_ids" not in json.dumps(result)
    assert PACKAGE not in json.dumps(result)
    assert "1.0" not in json.dumps(result)
    assert result["safe_to_apply"] is False
