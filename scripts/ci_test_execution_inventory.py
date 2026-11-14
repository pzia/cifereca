#!/usr/bin/env python3
"""Opt-in #5376 inventory of actual pytest outcomes, separate from collection.

No PR-wide extra pytest run. Record only selected items, sanitized phases and
classification evidence: no tracebacks, captured output, URLs or credentials.
A report is published atomically only for a complete run at a clean Git HEAD.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

try:
    from scripts.ci_test_collection_inventory import (
        ROOT, MANIFEST, _SHA_RE, _SUITE_RE, _PATH_RE,
        CollectionCapture, classify, load_classifications,
    )
except ModuleNotFoundError:
    from ci_test_collection_inventory import (
        ROOT, MANIFEST, _SHA_RE, _SUITE_RE, _PATH_RE,
        CollectionCapture, classify, load_classifications,
    )

OUTCOMES = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")


class ExecutionCapture(CollectionCapture):
    """Read hooks from the actual pytest run, not skip/xfail markers."""

    def __init__(self) -> None:
        super().__init__()
        self.deselected: list[str] = []
        self.phases: dict[str, dict[str, dict[str, Any]]] = {}
        self.duplicate_phases: list[str] = []
        self.collection_errors = 0

    def pytest_deselected(self, items: list[Any]) -> None:
        self.deselected.extend(item.nodeid for item in items)

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self.collection_errors += 1

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when not in {"setup", "call", "teardown"}:
            return
        phases = self.phases.setdefault(report.nodeid, {})
        if report.when in phases:
            self.duplicate_phases.append(report.nodeid)
        phases[report.when] = {
            "outcome": report.outcome,
            "wasxfail": bool(getattr(report, "wasxfail", False)),
            "duration": max(0.0, float(report.duration)),
        }


def outcome_for(phases: dict[str, dict[str, Any]]) -> str | None:
    """Derive terminal outcome with teardown errors taking precedence."""
    teardown = phases.get("teardown")
    if teardown and teardown["outcome"] == "failed":
        return "error"
    setup = phases.get("setup")
    if setup and setup["outcome"] == "failed":
        return "error"
    call = phases.get("call")
    if call:
        if call["outcome"] == "failed":
            return "failed"
        if call["outcome"] == "skipped":
            return "xfailed" if call["wasxfail"] else "skipped"
        if call["outcome"] == "passed":
            return "xpassed" if call["wasxfail"] else "passed"
    if setup and setup["outcome"] == "skipped":
        return "xfailed" if setup["wasxfail"] else "skipped"
    return None


def make_execution_report(
    capture: ExecutionCapture, manifest: dict[str, dict[str, str]],
    *, suite: str, source_sha: str | None, exit_code: int,
    clean_head: bool, selection_scoped: bool = False,
) -> dict[str, Any]:
    if capture.duplicate_phases:
        raise ValueError("duplicate_execution_phase")
    selected = [item["nodeid"] for item in capture.items]
    if len(selected) != len(set(selected)):
        raise ValueError("duplicate_collected_nodeid")
    if len(capture.deselected) != len(set(capture.deselected)):
        raise ValueError("duplicate_deselected_nodeid")
    if set(selected) & set(capture.deselected):
        raise ValueError("selected_deselected_overlap")
    if set(capture.phases) - set(selected):
        raise ValueError("unselected_execution_report")
    if any(not nodeid.startswith(suite + "/") for nodeid in selected + capture.deselected):
        raise ValueError("nodeid_outside_suite")

    records = []
    for item in capture.items:
        nodeid = item["nodeid"]
        phases = capture.phases.get(nodeid, {})
        outcome = outcome_for(phases)
        records.append({
            "nodeid": nodeid,
            **classify(nodeid, manifest),
            "outcome": outcome,
            "called": "call" in phases,
            "attempted": "setup" in phases,
            "duration_seconds": round(sum(
                phase["duration"] for phase in phases.values()
            ), 6),
        })
    records.sort(key=lambda item: item["nodeid"])
    totals = Counter(item["outcome"] for item in records)
    matrix = Counter(
        (item["owner_architecture"], item["business_object"],
         item["test_level"], item["outcome"])
        for item in records
    )
    complete = (
        exit_code in (0, 1)
        and capture.collection_errors == 0
        and bool(records)
        and all(
            row["outcome"] in OUTCOMES
            and row["attempted"]
            and "teardown" in capture.phases.get(row["nodeid"], {})
            for row in records
        )
        and (
            (exit_code == 0 and not totals["failed"] and not totals["error"])
            or (exit_code == 1 and (totals["failed"] or totals["error"]))
        )
    )
    return {
        "schema": "cifereca.pytest-execution-inventory/1",
        "suite": suite,
        "selection_scoped": selection_scoped,
        "source_sha": source_sha,
        "clean_head": clean_head,
        "exit_code": exit_code,
        "collection_errors": capture.collection_errors,
        "complete": complete,
        "count_selected": len(records),
        "count_deselected": len(capture.deselected),
        "count_attempted": sum(bool(row["attempted"]) for row in records),
        "count_executed": sum(bool(row["called"]) for row in records),
        "outcomes": {status: totals[status] for status in OUTCOMES},
        "reported_phase_duration_seconds": round(
            sum(row["duration_seconds"] for row in records), 6
        ),
        "note": (
            "A selected case is not necessarily executed: setup skips/failures "
            "do not enter its call phase. xfail/skip here are actual outcomes, "
            "not declared markers. Only this suite/selection and this execution "
            "are measured; these counts do not imply global pytest coverage."
        ),
        "matrix": [
            {"owner_architecture": owner, "business_object": business,
             "test_level": level, "outcome": status, "count": count}
            for (owner, business, level, status), count in sorted(matrix.items())
        ],
        "items": records,
    }


def write_verified_execution(report: dict[str, Any], output: Path) -> None:
    """Keep the prior report on interrupted/partial runs or dirty sources.

    Complete failing runs may publish as failures; they never masquerade as
    success because exit_code and outcomes are explicit.
    """
    if not report["complete"]:
        raise ValueError("incomplete_execution")
    if not report["clean_head"]:
        raise ValueError("uncommitted_tracked_changes")
    if not isinstance(report["source_sha"], str) or not _SHA_RE.fullmatch(report["source_sha"]):
        raise ValueError("missing_exact_git_head")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=".cifereca-executed-", suffix=".tmp", delete=False,
        ) as stream:
            tmp = Path(stream.name)
            stream.write(payload)
        os.replace(tmp, output)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _git_state(root: Path) -> tuple[str | None, bool]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root,
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root,
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, False
    return (sha if _SHA_RE.fullmatch(sha) else None), not bool(dirty)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="tests or src/plugins/<package>/tests")
    parser.add_argument("--nodeid", action="append", default=[],
                        help="Optional repeated exact pytest node ID within --suite")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not _SUITE_RE.fullmatch(args.suite):
        parser.error("noncanonical suite")
    suite_dir = ROOT / args.suite
    if not suite_dir.is_dir() or suite_dir.is_symlink():
        parser.error("missing or symlinked suite")
    for nodeid in args.nodeid:
        path = nodeid.split("::", 1)[0]
        candidate = ROOT / path
        if (not path.startswith(args.suite + "/")
                or not _PATH_RE.fullmatch(path)
                or ".." in Path(path).parts
                or not candidate.is_file() or candidate.is_symlink()):
            parser.error("nodeid must name an existing test file within suite")
    try:
        mapping = (
            load_classifications(args.manifest)
            if args.manifest is not None else
            load_classifications(MANIFEST, fragments=MANIFEST.with_suffix(".d"))
        )
    except ValueError as exc:
        parser.error(str(exc))
    before_sha, before_clean = _git_state(ROOT)
    import pytest
    capture = ExecutionCapture()
    exit_code = int(pytest.main(
        ["-q", *(args.nodeid if args.nodeid else [args.suite])],
        plugins=[capture],
    ))
    after_sha, after_clean = _git_state(ROOT)
    try:
        report = make_execution_report(
            capture, mapping, suite=args.suite,
            source_sha=before_sha if before_sha == after_sha else None,
            clean_head=before_clean and after_clean,
            exit_code=exit_code, selection_scoped=bool(args.nodeid),
        )
        write_verified_execution(report, args.output)
    except ValueError as exc:
        print(f"Execution inventory not published: {exc}", file=sys.stderr)
        return 1
    print(
        f"selected={report['count_selected']} attempted={report['count_attempted']} "
        f"executed={report['count_executed']} outcomes={report['outcomes']} "
        f"exit_code={exit_code}"
    )
    return 0 if exit_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
