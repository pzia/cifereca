"""Compare existing AST worklists by ownership, file, function and signature."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from scripts.ci_provider_ast_function_worklist import compare_counts

PREFIX = "src/cifereca/"
AGENT_A = frozenset({
    "core/adept_borrower.py",
    "core/adept_inventory_reconciliation.py",
    "core/adept_origin_audit.py",
    "core/adept_resolution_snapshot.py",
    "core/loan_adept_profile_guard.py",
    "core/loan_acquisition_admission.py",
})
AGENT_B = frozenset({
    "services.py", "brokers.py", "core/provider_sessions.py", "core/sync_coordination.py",
})
GROUPS = ("A", "B", "C", "CoreUI", "Transversal")
METRIC = "core.provider.ast-by-function/1"


def group_for(path: str) -> str:
    relative = path.removeprefix(PREFIX)
    if relative in AGENT_A:
        return "A"
    if relative in AGENT_B or relative.startswith("plugin_api/"):
        return "B"
    if relative.startswith("core/"):
        return "C"
    if relative.startswith("gui/"):
        return "CoreUI"
    return "Transversal"


def _validate(report):
    entries = report["entries"]
    unique = {(entry["path"], entry["line"]) for entry in entries}
    if report["metric"] != METRIC:
        raise ValueError("unsupported AST worklist metric")
    if len(unique) != len(entries) or len(unique) != report["unique_lines"]:
        raise ValueError("inconsistent AST unique-line count")
    if any(not path.startswith(PREFIX) for path, _ in unique):
        raise ValueError("AST path outside the installed Core")


def _counts(report):
    return Counter(group_for(entry["path"]) for entry in report["entries"])


def _signatures(report):
    return Counter(
        (entry["path"], entry["function"], tuple(sorted(entry["kinds"])),
         tuple(sorted(entry["providers"])))
        for entry in report["entries"]
    )


def summarize(current, baseline=None):
    _validate(current)
    groups = _counts(current)
    result = {
        "metric": current["metric"],
        "unique_lines": current["unique_lines"],
        "parse_errors": current["parse_errors"],
        "groups": {group: groups[group] for group in GROUPS},
        "limits": (
            "Counts cover scanned generic Core lines only. Signatures are static "
            "candidates. Same-signature replacements and indirect dispatch can be missed."
        ),
    }
    if baseline is None:
        return result
    _validate(baseline)
    result["baseline_parse_errors"] = baseline["parse_errors"]
    if current["parse_errors"] or baseline["parse_errors"]:
        result["comparison_unavailable"] = "incomplete AST worklist"
        return result

    before = _counts(baseline)
    result["group_deltas"] = {group: groups[group] - before[group] for group in GROUPS}
    result["comparison"] = compare_counts(baseline, current)
    before_files = Counter(entry["path"] for entry in baseline["entries"])
    after_files = Counter(entry["path"] for entry in current["entries"])
    result["changed_files"] = [
        {"path": path, "before": before_files[path], "after": after_files[path]}
        for path in sorted(before_files.keys() | after_files.keys())
        if before_files[path] != after_files[path]
    ]
    result["increased_signatures"] = [
        {"path": path, "function": function, "kinds": kinds,
         "providers": providers, "increase": count}
        for (path, function, kinds, providers), count in sorted(
            (_signatures(current) - _signatures(baseline)).items()
        )
    ]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current", type=Path, help="JSON worklist for the current revision")
    parser.add_argument("--baseline", type=Path, help="JSON worklist for the base revision")
    args = parser.parse_args(argv)
    try:
        current = json.loads(args.current.read_text(encoding="utf-8"))
        baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
        report = summarize(current, baseline)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(report["parse_errors"] or report.get("baseline_parse_errors")))


if __name__ == "__main__":
    raise SystemExit(main())
