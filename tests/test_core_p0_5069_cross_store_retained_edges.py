"""P0 #5069 B9: qualified retention edges are descriptive, not purge authority."""
from __future__ import annotations

import json

from cifereca import updater
from cifereca.updater_retention_cross_store import (
    _plugin_version_id, inspect_transaction_plugin_references,
)
from cifereca.updater_retention_inventory import _name_id

PACKAGE = "org.synthetic.provider"


def _transaction(root, name, version):
    folder = root / "transactions" / name
    folder.mkdir(parents=True)
    record = {
        "schema": updater.TRANSACTION_SCHEMA,
        "id": name,
        "state": "applied",
        "created_at": "20261108T101214Z",
        "packages": {
            PACKAGE: {
                "before": {"exists": True, "versions": [version]},
                "after": {"exists": True, "versions": [version]},
            },
        },
    }
    (folder / "transaction.json").write_text(json.dumps(record))


def _core_plan(*pairs):
    return {"objects": [
        {"object_id": _name_id("transaction", name), "decision": decision}
        for name, decision in pairs
    ]}


def _plugins(*versions):
    return {"objects": [
        {"object_id": _plugin_version_id(PACKAGE, version), "reasons": []}
        for version in versions
    ]}


def test_retained_and_candidate_transactions_are_distinct_proofs(tmp_path):
    root = tmp_path / "runtime"
    first, second = "20261108T101214Z-a", "20261108T101215Z-b"
    _transaction(root, first, "1.0")
    _transaction(root, second, "2.0")
    report = inspect_transaction_plugin_references(
        root, _plugins("1.0", "2.0"),
        _core_plan((first, "retain"), (second, "candidate")),
    )
    assert report["coverage"] == ["transaction_plugin_version_edges"]
    assert report["retained_transaction_edges"] == 1
    assert report["candidate_transaction_edges"] == 1
    assert report["retained_unresolved_edges"] == 0
    assert report["plugin_version_edges"] == 2
    assert report["safe_to_apply"] is False
    assert PACKAGE not in json.dumps(report)


def test_missing_retained_version_blocks_without_authorizing_candidate(tmp_path):
    root = tmp_path / "runtime"
    first, second = "20261108T101214Z-a", "20261108T101215Z-b"
    _transaction(root, first, "1.0")
    _transaction(root, second, "2.0")
    report = inspect_transaction_plugin_references(
        root, _plugins("2.0"),
        _core_plan((first, "retain"), (second, "candidate")),
    )
    assert report["retained_unresolved_edges"] == 1
    assert report["unresolved_edges"] == 1
    assert "transaction_plugin_version_missing" in report["blockers"]
    assert report["safe_to_apply"] is False


def test_incomplete_or_forged_transaction_plan_fails_closed(tmp_path):
    root = tmp_path / "runtime"
    name = "20261108T101214Z-a"
    _transaction(root, name, "1.0")
    key = _name_id("transaction", name)
    for plan in (
        {"objects": []},
        {"objects": [
            {"object_id": key, "decision": "candidate"},
            {"object_id": key, "decision": "retain"},
        ]},
        {"objects": [{"object_id": key, "decision": "delete"}]},
        {"objects": [{"object_id": "transaction:" + "f" * 64,
                      "decision": "retain"}]},
        {"objects": None},
    ):
        report = inspect_transaction_plugin_references(root, _plugins("1.0"), plan)
        assert report["coverage"] == []
        assert report["retained_transaction_edges"] is None
        assert report["candidate_transaction_edges"] is None
        assert "transaction_retention_inventory_mismatch" in report["blockers"]
        assert report["safe_to_apply"] is False


def test_same_version_retained_and_candidate_is_shared_dependency(tmp_path):
    root = tmp_path / "runtime"
    first, second = "20261108T101214Z-a", "20261108T101215Z-b"
    _transaction(root, first, "1.0")
    _transaction(root, second, "1.0")
    report = inspect_transaction_plugin_references(
        root, _plugins("1.0"),
        _core_plan((first, "retain"), (second, "candidate")),
    )
    assert report["plugin_version_edges"] == 1
    assert report["retained_transaction_edges"] == 1
    assert report["candidate_transaction_edges"] == 1
    assert report["safe_to_apply"] is False
