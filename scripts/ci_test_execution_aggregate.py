#!/usr/bin/env python3
"""Reconcile #5376 pytest execution reports by exact SHA and pytest nodeid.

Opt-in, offline post-processing. Never re-run tests or claim that two gates
provide distinct *cases* merely because both executed the same nodeid.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

SCHEMA = "cifereca.pytest-execution-inventory/1"
OUTCOMES = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_CLASSIFICATION = ("owner_architecture", "business_object", "test_level")


def validate_execution_report(report: Any) -> dict[str, Any]:
    """Reject incomplete/corrupted inputs before they affect any total."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise ValueError("invalid_execution_schema")
    sha = report.get("source_sha")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ValueError("invalid_source_sha")
    if report.get("complete") is not True or report.get("clean_head") is not True:
        raise ValueError("not_a_complete_clean_head_run")
    exit_code = report.get("exit_code")
    if type(exit_code) is not int or exit_code not in (0, 1):
        raise ValueError("invalid_exit_code")
    items = report.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("missing_executed_items")
    selected = report.get("count_selected")
    if type(selected) is not int or selected != len(items):
        raise ValueError("selected_count_mismatch")
    if type(report.get("count_deselected")) is not int or report["count_deselected"] < 0:
        raise ValueError("invalid_deselection_count")
    if not isinstance(report.get("suite"), str) or not report["suite"]:
        raise ValueError("missing_suite")
    if type(report.get("selection_scoped")) is not bool:
        raise ValueError("missing_selection_scope")
    seen: set[str] = set()
    computed = Counter()
    attempts = calls = 0
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("malformed_item")
        nodeid, outcome = row.get("nodeid"), row.get("outcome")
        if not isinstance(nodeid, str) or not nodeid or nodeid in seen:
            raise ValueError("invalid_or_duplicate_nodeid")
        seen.add(nodeid)
        if not nodeid.startswith(report["suite"] + "/") or outcome not in OUTCOMES:
            raise ValueError("item_outside_suite_or_invalid_outcome")
        if type(row.get("attempted")) is not bool or type(row.get("called")) is not bool:
            raise ValueError("invalid_execution_phases")
        if row["called"] and not row["attempted"]:
            raise ValueError("called_without_setup")
        for key in _CLASSIFICATION:
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError("missing_classification")
        attempts += row["attempted"]
        calls += row["called"]
        computed[outcome] += 1
    if (
        type(report.get("count_attempted")) is not int
        or type(report.get("count_executed")) is not int
        or report["count_attempted"] != attempts
        or report["count_executed"] != calls
    ):
        raise ValueError("phase_counts_mismatch")
    outcomes = report.get("outcomes")
    if (
        not isinstance(outcomes, dict)
        or set(outcomes) != set(OUTCOMES)
        or any(type(outcomes[o]) is not int or outcomes[o] != computed[o] for o in OUTCOMES)
    ):
        raise ValueError("outcome_counts_mismatch")
    if (exit_code == 0 and (computed["failed"] or computed["error"])) or (
        exit_code == 1 and not (computed["failed"] or computed["error"])
    ):
        raise ValueError("run_exit_outcome_mismatch")
    return report


def aggregate(gates: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    if len(gates) < 2:
        raise ValueError("at_least_two_gates_required")
    labels: set[str] = set()
    source_sha: str | None = None
    cases: dict[str, dict[str, Any]] = {}
    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    execution_outcomes = Counter()
    gate_rows = []
    for label, report in gates:
        if not isinstance(label, str) or not _LABEL.fullmatch(label) or label in labels:
            raise ValueError("invalid_or_duplicate_gate_label")
        labels.add(label)
        report = validate_execution_report(report)
        if source_sha is None:
            source_sha = report["source_sha"]
        elif source_sha != report["source_sha"]:
            raise ValueError("mixed_source_shas")
        gate_rows.append({
            "gate": label,
            "suite": report["suite"],
            "selection_scoped": report["selection_scoped"],
            "count_selected": report["count_selected"],
            "count_executed": report["count_executed"],
            "outcomes": report["outcomes"],
            "exit_code": report["exit_code"],
        })
        for item in report["items"]:
            nodeid = item["nodeid"]
            classification = {k: item[k] for k in _CLASSIFICATION}
            prior = cases.setdefault(nodeid, classification)
            if prior != classification:
                raise ValueError("conflicting_classification:" + nodeid)
            observations[nodeid].append({
                "gate": label, "outcome": item["outcome"],
                "called": item["called"], "attempted": item["attempted"],
            })
            execution_outcomes[item["outcome"]] += 1

    matrix: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"unique_cases": 0, "selected_observations": 0,
                 "called_executions": 0}
    )
    for nodeid, classification in cases.items():
        key = tuple(classification[k] for k in _CLASSIFICATION)
        matrix[key]["unique_cases"] += 1
        matrix[key]["selected_observations"] += len(observations[nodeid])
        matrix[key]["called_executions"] += sum(
            item["called"] for item in observations[nodeid]
        )
    replays = [
        {
            "nodeid": nodeid,
            **cases[nodeid],
            "observations": observations[nodeid],
            "discordant_outcomes": len({
                row["outcome"] for row in observations[nodeid]
            }) > 1,
        }
        for nodeid in sorted(cases)
        if len(observations[nodeid]) > 1
    ]
    total_observations = sum(len(value) for value in observations.values())
    total_called = sum(
        item["called"] for values in observations.values() for item in values
    )
    repeated_calls = sum(
        max(0, sum(item["called"] for item in values) - 1)
        for values in observations.values()
    )
    return {
        "schema": "cifereca.pytest-execution-aggregate/1",
        "source_sha": source_sha,
        "gate_count": len(gate_rows),
        "count_unique_cases": len(cases),
        "count_selected_observations": total_observations,
        "count_called_executions": total_called,
        "count_replayed_cases": len(replays),
        "count_extra_selected_observations": total_observations - len(cases),
        "count_reexecuted_cases": sum(
            sum(item["called"] for item in values) > 1
            for values in observations.values()
        ),
        "count_extra_called_executions": repeated_calls,
        "count_discordant_replayed_cases": sum(row["discordant_outcomes"] for row in replays),
        "execution_outcomes": {name: execution_outcomes[name] for name in OUTCOMES},
        "all_gates_passed": all(row["exit_code"] == 0 for row in gate_rows),
        "gates": gate_rows,
        "matrix": [
            {**dict(zip(_CLASSIFICATION, key)), **counts}
            for key, counts in sorted(matrix.items())
        ],
        "replayed_cases": replays,
        "note": (
            "Unique cases are deduplicated by pytest nodeid at one exact SHA. "
            "Selected observations include skips; only cases reaching the "
            "pytest call phase count as executed. Gate labels are user-declared, "
            "not proof of independent runs. No semantic coverage is inferred."
        ),
    }


def write_atomic(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=".cifereca-aggregate-", suffix=".tmp", delete=False,
        ) as stream:
            tmp = Path(stream.name)
            json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
        os.replace(tmp, output)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", action="append", required=True, metavar="LABEL=PATH",
                        help="One complete execution report per named CI gate")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    gates: list[tuple[str, dict[str, Any]]] = []
    hashes: set[str] = set()
    try:
        for entry in args.gate:
            if "=" not in entry:
                raise ValueError("expected_label_equals_path")
            label, source = entry.split("=", 1)
            path = Path(source)
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            # A byte-identical copy is not evidence of a second test run.
            if digest in hashes:
                raise ValueError("duplicate_report_evidence")
            hashes.add(digest)
            gates.append((label, json.loads(content)))
        result = aggregate(gates)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"Execution aggregation refused: {exc}", file=sys.stderr)
        return 1
    write_atomic(result, args.output)
    print(
        f"unique={result['count_unique_cases']} "
        f"selected_observations={result['count_selected_observations']} "
        f"called_executions={result['count_called_executions']} "
        f"extra_calls={result['count_extra_called_executions']} "
        f"discordant={result['count_discordant_replayed_cases']}"
    )
    return 0 if result["all_gates_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
