"""#5376: a repeated pytest node ID is one case, not a new case."""
from __future__ import annotations

from collections import Counter
import copy
import json

import pytest

from scripts.ci_test_execution_aggregate import (
    aggregate, main, validate_execution_report, write_atomic,
)

SHA = "a" * 40
SHB = "b" * 40
OWNER = {
    "owner_architecture": "core",
    "business_object": "ci_quality",
    "test_level": "contract",
}


def _run(cases, *, sha=SHA, suite="tests", exit_code=0):
    """Synthetic complete execution report matching the real runner contract."""
    items = [
        {
            "nodeid": node,
            **OWNER,
            "outcome": outcome,
            "attempted": True,
            "called": outcome != "skipped",
            "duration_seconds": 0.1,
        }
        for node, outcome in cases
    ]
    outcomes = Counter(item["outcome"] for item in items)
    return {
        "schema": "cifereca.pytest-execution-inventory/1",
        "source_sha": sha, "complete": True, "clean_head": True,
        "suite": suite, "selection_scoped": True,
        "exit_code": exit_code, "count_selected": len(items),
        "count_deselected": 0, "count_attempted": len(items),
        "count_executed": sum(item["called"] for item in items),
        "outcomes": {
            key: outcomes[key] for key in (
                "passed", "failed", "error", "skipped", "xfailed", "xpassed",
            )
        },
        "items": items,
    }


def test_aggregate_distinguishes_unique_cases_replays_and_disagreement():
    left = _run([
        ("tests/test_a.py::test_case[0]", "passed"),
        ("tests/test_b.py::test_other", "passed"),
    ])
    right = _run([
        ("tests/test_a.py::test_case[0]", "skipped"),
        ("tests/test_c.py::test_fail", "failed"),
    ], exit_code=1)
    report = aggregate([("core", left), ("rerun", right)])
    assert report["source_sha"] == SHA
    assert report["gate_count"] == 2
    assert report["count_unique_cases"] == 3
    assert report["count_selected_observations"] == 4
    assert report["count_called_executions"] == 3
    assert report["count_replayed_cases"] == 1
    assert report["count_extra_selected_observations"] == 1
    assert report["count_extra_called_executions"] == 0
    assert report["count_discordant_replayed_cases"] == 1
    assert report["execution_outcomes"]["passed"] == 2
    assert report["execution_outcomes"]["skipped"] == 1
    assert report["execution_outcomes"]["failed"] == 1
    assert not report["all_gates_passed"]
    assert report["replayed_cases"][0]["nodeid"] == "tests/test_a.py::test_case[0]"
    assert report["matrix"] == [{
        **OWNER, "unique_cases": 3, "selected_observations": 4,
        "called_executions": 3,
    }]


def test_same_case_and_outcome_in_multiple_gates_is_not_semantic_new_coverage():
    row = "tests/test_security.py::test_critical"
    report = aggregate([
        ("PR", _run([(row, "passed")])),
        ("main", _run([(row, "passed")])),
        ("manual", _run([(row, "passed")])),
    ])
    assert report["count_unique_cases"] == 1
    assert report["count_selected_observations"] == 3
    assert report["count_called_executions"] == 3
    assert report["count_extra_selected_observations"] == 2
    assert report["count_extra_called_executions"] == 2
    assert report["count_reexecuted_cases"] == 1
    assert report["count_discordant_replayed_cases"] == 0
    assert report["all_gates_passed"]


@pytest.mark.parametrize("alter", [
    lambda x: x.update(schema="invented"),
    lambda x: x.update(source_sha="bad"),
    lambda x: x.update(complete=False),
    lambda x: x.update(clean_head=False),
    lambda x: x.update(exit_code=1),
    lambda x: x.update(count_selected=999),
    lambda x: x.update(count_executed=999),
    lambda x: x.update(count_attempted=999),
    lambda x: x.update(count_deselected=-1),
    lambda x: x.update(selection_scoped="yes"),
    lambda x: x["outcomes"].update(passed=42),
    lambda x: x["items"][0].update(nodeid="src/plugins/p/tests/test_a.py::test_a"),
    lambda x: x["items"][0].update(called=True, attempted=False),
    lambda x: x["items"][0].update(outcome="incomplete"),
    lambda x: x["items"][0].pop("owner_architecture"),
])
def test_corrupted_gate_data_is_rejected(alter):
    data = _run([("tests/test_a.py::test_a", "passed")])
    alter(data)
    with pytest.raises(ValueError):
        validate_execution_report(data)


def test_gate_metadata_conflicts_are_not_silently_reclassified():
    row = "tests/test_shared.py::test_shared"
    first = _run([(row, "passed")])
    second = _run([(row, "passed")])
    second["items"][0]["owner_architecture"] = "coreui"
    with pytest.raises(ValueError, match="conflicting_classification"):
        aggregate([("core", first), ("ui", second)])


def test_mixed_shas_and_duplicate_gate_labels_fail_closed():
    case = ("tests/test_a.py::test_a", "passed")
    with pytest.raises(ValueError, match="mixed_source_shas"):
        aggregate([("PR", _run([case])), ("main", _run([case], sha=SHB))])
    with pytest.raises(ValueError, match="invalid_or_duplicate_gate_label"):
        aggregate([("PR", _run([case])), ("PR", _run([case]))])
    with pytest.raises(ValueError, match="at_least_two_gates"):
        aggregate([("PR", _run([case]))])


def test_duplicate_item_within_one_gate_cannot_inflate_unique_cases():
    item = ("tests/test_a.py::test_a", "passed")
    gate = _run([item, item])
    with pytest.raises(ValueError, match="invalid_or_duplicate_nodeid"):
        aggregate([("gate1", gate), ("gate2", _run([item]))])


def test_cli_rejects_byte_identical_copies_as_distinct_gate_proof(tmp_path):
    gate = _run([("tests/test_a.py::test_a", "passed")])
    first = tmp_path / "first.json"
    duplicate = tmp_path / "duplicated.json"
    for path in (first, duplicate):
        path.write_text(json.dumps(gate), encoding="utf-8")
    output = tmp_path / "last-good.json"
    output.write_text('{"last_good":true}', encoding="utf-8")
    assert main([
        "--gate", f"PR={first}", "--gate", f"main={duplicate}",
        "--output", str(output),
    ]) == 1
    assert json.loads(output.read_text()) == {"last_good": True}


def test_cli_writes_complete_failed_gate_with_exit_status_one(tmp_path):
    first = tmp_path / "core.json"
    second = tmp_path / "plugin.json"
    first.write_text(json.dumps(_run([("tests/test_a.py::test_a", "passed")])))
    second.write_text(json.dumps(_run(
        [("tests/test_b.py::test_b", "failed")], exit_code=1,
    )))
    output = tmp_path / "aggregate.json"
    assert main([
        "--gate", f"core={first}", "--gate", f"plugin={second}",
        "--output", str(output),
    ]) == 1
    result = json.loads(output.read_text())
    assert not result["all_gates_passed"]
    assert result["count_unique_cases"] == 2
    assert result["count_selected_observations"] == 2
    assert result["count_called_executions"] == 2


def test_atomic_output_avoids_temporary_files(tmp_path):
    output = tmp_path / "aggregate.json"
    write_atomic({"status": "current"}, output)
    assert json.loads(output.read_text()) == {"status": "current"}
    assert list(tmp_path.iterdir()) == [output]


def test_input_rows_are_not_mutated_by_aggregation():
    a = _run([("tests/test_a.py::test_a", "passed")])
    b = _run([("tests/test_a.py::test_a", "failed")], exit_code=1)
    snapshot = copy.deepcopy((a, b))
    aggregate([("a", a), ("b", b)])
    assert (a, b) == snapshot
