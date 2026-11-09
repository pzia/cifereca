"""#5069 B8: transaction-plugin graph edges are diagnostic, never GC authority."""
from __future__ import annotations

import json

import pytest

from cifereca import updater
from cifereca.updater_retention_cross_store import (
    _plugin_version_id, inspect_transaction_plugin_references,
)


PACKAGE = "org.synthetic.provider"
TX = "20261108T101214Z-2b88f133f09d"


def _write_transaction(runtime, *, version_before="1.2.0",
                       version_after="1.2.1", name=TX, packages=None):
    directory = runtime / "transactions" / name
    directory.mkdir(parents=True)
    record = {
        "schema": updater.TRANSACTION_SCHEMA, "id": name,
        "created_at": "20261108T101214Z", "state": "applied",
        "packages": packages if packages is not None else {
            PACKAGE: {
                "before": {"exists": True, "versions": [version_before]},
                "after": {"exists": True, "versions": [version_after]},
            },
        },
    }
    (directory / "transaction.json").write_text(json.dumps(record))
    return directory


def _plan(*versions):
    return {"objects": [
        {"object_id": _plugin_version_id(PACKAGE, version), "reasons": []}
        for version in versions
    ]}


def test_absent_store_is_readonly_but_not_gc_eligibility(tmp_path):
    root = tmp_path / "runtime"
    report = inspect_transaction_plugin_references(root, _plan())
    assert report["transactions"] == 0
    assert report["plugin_version_edges"] == 0
    assert report["coverage"] == ["transaction_plugin_version_edges"]
    assert report["safe_to_apply"] is False
    assert not root.exists()


def test_exact_before_after_versions_resolve_without_leaking_package_names(tmp_path):
    root = tmp_path / "runtime"
    journal = _write_transaction(root)
    before = journal.stat().st_mtime_ns
    report = inspect_transaction_plugin_references(root, _plan("1.2.0", "1.2.1"))
    assert report["transactions"] == 1
    assert report["plugin_version_edges"] == 2
    assert report["resolved_edges"] == 2
    assert report["unresolved_edges"] == 0
    assert report["safe_to_apply"] is False
    assert journal.stat().st_mtime_ns == before
    assert PACKAGE not in json.dumps(report)
    assert str(root) not in json.dumps(report)


def test_missing_historical_version_is_blocker_not_orphan_purge_license(tmp_path):
    root = tmp_path / "runtime"
    _write_transaction(root)
    report = inspect_transaction_plugin_references(root, _plan("1.2.1"))
    assert report["resolved_edges"] == 1
    assert report["unresolved_edges"] == 1
    assert "transaction_plugin_version_missing" in report["blockers"]
    assert report["safe_to_apply"] is False


@pytest.mark.parametrize("bad", [
    {PACKAGE: {"before": {"exists": True, "versions": ["../private"]}}},
    {PACKAGE: {"before": {"exists": True, "versions": [True]}}},
    {PACKAGE: {"before": {"exists": True, "versions": ["1.2", "1.2"]}}},
    {PACKAGE: {"before": {"exists": "true", "versions": ["1.2"]}}},
    {"../../secrets": {"before": {"exists": True, "versions": ["1.2"]}}},
])
def test_malformed_or_private_references_refused_without_echo(tmp_path, bad):
    root = tmp_path / "runtime"
    _write_transaction(root, packages=bad)
    report = inspect_transaction_plugin_references(root, _plan("1.2"))
    assert "transaction_plugin_reference_unqualified" in report["blockers"]
    assert report["coverage"] == []
    assert report["safe_to_apply"] is False
    assert "../../secrets" not in json.dumps(report)


def test_unqualified_plugin_version_is_not_a_resolved_reference(tmp_path):
    root = tmp_path / "runtime"
    _write_transaction(root)
    plan = _plan("1.2.0", "1.2.1")
    plan["objects"][0]["reasons"] = ["unowned", "unknown_timestamp"]
    report = inspect_transaction_plugin_references(root, plan)
    assert report["resolved_edges"] == 1
    assert report["unresolved_edges"] == 1
    assert "plugin_reference_ownership_unqualified" in report["blockers"]
    assert report["safe_to_apply"] is False




def test_seven_transactions_and_two_versions_do_not_imply_deletion(tmp_path):
    root = tmp_path / "runtime"
    for idx in range(7):
        _write_transaction(
            root, name=f"20261108T10121{idx}Z-2b88f133f09d",
            version_before="1.2.0", version_after="1.2.1",
        )
    report = inspect_transaction_plugin_references(root, _plan("1.2.0", "1.2.1"))
    assert report["transactions"] == 7
    assert report["plugin_version_edges"] == 2
    assert report["resolved_edges"] == 2
    assert report["safe_to_apply"] is False


def test_symbolic_link_never_exposes_private_transaction(tmp_path):
    root = tmp_path / "runtime"
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "transaction.json").write_text("PRIVATE_CONTENT")
    txdir = root / "transactions"
    txdir.mkdir(parents=True)
    (txdir / TX).symlink_to(outside, target_is_directory=True)
    report = inspect_transaction_plugin_references(root, _plan())
    assert report["transactions"] == 0
    assert "unsafe_transaction_reference_entry" in report["blockers"]
    assert "PRIVATE_CONTENT" not in json.dumps(report)
    assert report["safe_to_apply"] is False


def test_official_retention_command_keeps_cross_store_incomplete(tmp_path, monkeypatch, capsys):
    root = tmp_path / "runtime"
    _write_transaction(root)
    monkeypatch.setattr(updater, "install_root", lambda: root)
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: tmp_path / "packages")
    monkeypatch.setattr(updater, "state_dir", lambda **kw: tmp_path / "state")
    assert updater.main(["--retention-inspect", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    edges = report["cross_store_reference_inspection"]
    assert edges["plugin_version_edges"] == 2
    assert edges["unresolved_edges"] == 2
    assert report["safe_to_apply"] is False
    assert "cross_store_reference_proofs" in report["pending_stores"]


def _core_decisions(*rows):
    from hashlib import sha256
    return {"objects": [
        {"object_id": "transaction:" + sha256(name.encode("utf-8")).hexdigest(),
         "decision": decision, "reasons": ["minimum_generations"] if decision == "retain" else []}
        for name, decision in rows
    ]}


def test_retained_transaction_edges_are_distinct_from_historical_candidates(tmp_path):
    root = tmp_path / "runtime"
    older = "20261107T101214Z-2b88f133f09d"
    _write_transaction(root, name=older, version_before="1.0", version_after="1.1")
    _write_transaction(root, name=TX, version_before="1.1", version_after="1.2")
    plan = _core_decisions((older, "candidate"), (TX, "retain"))
    report = inspect_transaction_plugin_references(
        root, _plan("1.0", "1.1", "1.2"), core_transaction_plan=plan,
    )
    assert report["retention_edges_qualified"] is True
    assert report["plugin_version_edges"] == 3
    assert report["retained_transaction_plugin_edges"] == 2
    assert report["candidate_transaction_plugin_edges"] == 2
    assert report["resolved_retained_edges"] == 2
    assert report["unresolved_retained_edges"] == 0
    assert report["safe_to_apply"] is False


def test_missing_retained_plugin_remains_unresolved_and_never_permits_purge(tmp_path):
    root = tmp_path / "runtime"
    _write_transaction(root)
    report = inspect_transaction_plugin_references(
        root, _plan("1.2.1"), core_transaction_plan=_core_decisions((TX, "retain")),
    )
    assert report["retention_edges_qualified"] is True
    assert report["retained_transaction_plugin_edges"] == 2
    assert report["unresolved_retained_edges"] == 1
    assert "transaction_plugin_version_missing" in report["blockers"]
    assert report["safe_to_apply"] is False


@pytest.mark.parametrize("core_plan", [
    {"objects": []},
    {"objects": [{"object_id": "transaction:bad", "decision": "retain"}]},
    _core_decisions((TX, "retain"), (TX, "candidate")),
    _core_decisions((TX, "other")),
])
def test_unqualified_core_retention_cannot_claim_retained_edge_closure(tmp_path, core_plan):
    root = tmp_path / "runtime"
    _write_transaction(root)
    report = inspect_transaction_plugin_references(
        root, _plan("1.2.0", "1.2.1"), core_transaction_plan=core_plan,
    )
    assert report["retention_edges_qualified"] is False
    assert report["retained_transaction_plugin_edges"] is None
    assert "core_transaction_retention_unqualified" in report["blockers"]
    assert report["safe_to_apply"] is False


def test_malformed_transaction_revokes_entire_edge_retention_qualification(tmp_path):
    root = tmp_path / "runtime"
    _write_transaction(root)
    malformed = "20261108T101215Z-2b88f133f09d"
    _write_transaction(root, name=malformed, packages={"../secrets": {}})
    report = inspect_transaction_plugin_references(
        root, _plan("1.2.0", "1.2.1"),
        core_transaction_plan=_core_decisions((TX, "retain"), (malformed, "retain")),
    )
    assert report["retention_edges_qualified"] is False
    assert report["retained_transaction_plugin_edges"] is None
    assert report["safe_to_apply"] is False


def test_plugin_only_candidate_conflict_with_retained_rollback_is_reported(tmp_path):
    root = tmp_path / "runtime"
    _write_transaction(root)
    plan = _plan("1.2.0", "1.2.1", "0.9.0")
    for item in plan["objects"]:
        item["decision"] = "candidate"
    report = inspect_transaction_plugin_references(
        root, plan, core_transaction_plan=_core_decisions((TX, "retain")),
    )
    assert report["retained_transaction_plugin_edges"] == 2
    assert report["retained_plugin_candidate_conflicts"] == 2
    assert report["safe_to_apply"] is False


def test_retained_plugin_version_does_not_count_as_candidate_conflict(tmp_path):
    root = tmp_path / "runtime"
    _write_transaction(root)
    plan = _plan("1.2.0", "1.2.1")
    plan["objects"][0]["decision"] = "retain"
    plan["objects"][1]["decision"] = "candidate"
    report = inspect_transaction_plugin_references(
        root, plan, core_transaction_plan=_core_decisions((TX, "retain")),
    )
    assert report["retained_plugin_candidate_conflicts"] == 1
    assert report["safe_to_apply"] is False


def test_incomplete_plugin_decisions_do_not_invent_zero_conflicts(tmp_path):
    root = tmp_path / "runtime"
    _write_transaction(root)
    report = inspect_transaction_plugin_references(
        root, _plan("1.2.0", "1.2.1"),
        core_transaction_plan=_core_decisions((TX, "retain")),
    )
    assert report["retained_plugin_candidate_conflicts"] is None
    assert report["retention_edges_qualified"] is True
    assert report["safe_to_apply"] is False


def test_invalid_journal_disables_candidate_conflict_projection(tmp_path):
    root = tmp_path / "runtime"
    _write_transaction(root, packages={"../../private": {}})
    plan = _plan("1.2.0", "1.2.1")
    for item in plan["objects"]:
        item["decision"] = "candidate"
    report = inspect_transaction_plugin_references(
        root, plan, core_transaction_plan=_core_decisions((TX, "retain")),
    )
    assert report["retained_plugin_candidate_conflicts"] is None
    assert report["safe_to_apply"] is False


def test_unresolved_installed_version_cannot_report_zero_candidate_conflicts(tmp_path):
    root = tmp_path / "runtime"
    _write_transaction(root)
    plan = _plan("1.2.1")
    plan["objects"][0]["decision"] = "retain"
    report = inspect_transaction_plugin_references(
        root, plan, core_transaction_plan=_core_decisions((TX, "retain")),
    )
    assert report["unresolved_retained_edges"] == 1
    assert report["retained_plugin_candidate_conflicts"] is None
    assert report["safe_to_apply"] is False
