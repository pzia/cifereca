#!/usr/bin/env python3
"""Publish only bounded, allowlisted E2E verdicts, never raw GTK evidence (#1143)."""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1024 * 1024
MAX_ROWS = 128


def _allowed_scenarios() -> set[str]:
    # These are reviewed scenario identifiers from source, not user-supplied
    # test output. Never trust a scenario string from summary.json alone.
    spec = importlib.util.spec_from_file_location("cifereca_e2e_export_scenarios", ROOT / "e2e/run.py")
    if spec is None or spec.loader is None:
        raise ValueError("E2E scenario source unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.SCENARIOS)



# Closed labels from the in-repository diagnostic probe, never exception text.
_DIAGNOSTIC_STAGES = frozenset({
    "D16_full_mainwindow_native", "D16_full_mainwindow_narrow",
    "D16_full_mainwindow_restored", "P1_1889_sync_760_post_capture_normal_font",
    "P1_1889_sync_760_post_capture_large_font", "P0_1905_numilog_inventory_preflight",
    "P0_1905_numilog_inventory_click",
    "P0_1905_numilog_inventory_pointer_dispatch",
    "P0_1905_numilog_inventory_dialog_wait",
    "P0_1905_numilog_inventory_dialog_preflight",
    "P0_1905_numilog_inventory_dialog_ready",
    "P0_2027_narrow_header_minimum", "P0_2027_narrow_source_minimum",
    "P0_2027_narrow_scroll_minimum", "P0_2027_narrow_window_minimum",
    "P0_2027_narrow_allocation_pending",
    "P0_2140_resize_selected_window_760", "P0_2140_narrow_window_minimum",
    "P0_2140_narrow_allocation_pending", "P0_2140_wait_57_errors",
    "P0_2140_bulk_fixture",
})


def _failed_diagnostic(source: Path, scenario: str) -> dict:
    probes = {
        "mainwindow-baseline-1366": "diagnostics_navigation_probe.py",
        "mainwindow-baseline-1600": "diagnostics_navigation_probe.py",
        "diagnostics-1366": "diagnostics_page_probe.py",
        "diagnostics-large-1600": "diagnostics_page_probe.py",
    }
    if scenario not in probes:
        return {}
    # Workflow --out/scenario + runner's own scenario directory. Never follow
    # a path supplied by summary.json, a symlink or a large/unreadable report.
    root = source.resolve().parent
    directory = root / scenario
    nested = directory / scenario
    report = nested / "screen.json"
    try:
        if any(p.is_symlink() for p in (directory, nested, report)):
            return {}
        if not report.is_file() or report.stat().st_size > MAX_BYTES:
            return {}
        data = json.loads(report.read_text(encoding="utf-8"))
        if type(data) is not dict:
            return {}
        result = {}
        stage = data.get("stage")
        if type(stage) is str and stage in _DIAGNOSTIC_STAGES:
            result["last_stage"] = stage
        line = data.get("checkpoint_line")
        probe = ast.parse((ROOT / "e2e" / probes[scenario]).read_text())
        helper_allowed = probes[scenario] == "diagnostics_navigation_probe.py"
        helper_probe = ast.parse((ROOT / "e2e/activity_feedback_acceptance.py").read_text()) if helper_allowed else None
        checkpoint_helper = helper_allowed and data.get("checkpoint_source") == "activity_feedback"
        checkpoint_probe = helper_probe if checkpoint_helper else probe
        allowed_lines = {n.lineno for n in ast.walk(checkpoint_probe)
                         if isinstance(n, (ast.FunctionDef, ast.Lambda))}
        if type(line) is int and line in allowed_lines:
            result["checkpoint_line"] = line
            if checkpoint_helper:
                result["checkpoint_source"] = "activity_feedback"
        # A Diagnostics wait may time out inside the generic polling closure.
        # Publish only the numeric source line of a literal reviewed lambda.
        if probes[scenario] == "diagnostics_page_probe.py":
            wait_line = data.get("wait_line")
            allowed_waits = {n.lineno for n in ast.walk(probe)
                             if isinstance(n, ast.Lambda)}
            if type(wait_line) is int and wait_line in allowed_waits:
                result["wait_line"] = wait_line
        # A callback checkpoint may be a lambda calling another function.
        # Publish the actual failure location only when it is a reviewed
        # assertion/raise statement in this exact probe, never traceback text.
        failure_line = data.get("failure_line")
        failure_helper = helper_allowed and data.get("failure_source") == "activity_feedback"
        failure_probe = helper_probe if failure_helper else probe
        assertion_lines = {n.lineno for n in ast.walk(failure_probe)
                           if isinstance(n, (ast.Assert, ast.Raise))}
        if type(failure_line) is int and failure_line in assertion_lines:
            result["failure_line"] = failure_line
            if failure_helper:
                result["failure_source"] = "activity_feedback"
        minimum_row = data.get("minimum_row")
        if (probes[scenario] == "diagnostics_page_probe.py"
                and stage == "P0_2140_narrow_window_minimum"
                and type(minimum_row) is int and 0 <= minimum_row < 128):
            # A bounded index into the reviewed builder's children, never
            # widget labels, geometry, paths or arbitrary GTK object reprs.
            result["minimum_row"] = minimum_row
        duration = data.get("duration_seconds")
        if type(duration) in (int, float) and math.isfinite(duration) and 0 <= duration <= 3600:
            result["duration_seconds"] = round(duration, 2)
        # Equality against one fixed harness message; never publish its text.
        result["failure_kind"] = (
            "scenario_timeout" if type(data.get("error")) is str and data["error"] in {
                "Timeout Diagnostics navigation 50s", "Timeout Diagnostics navigation 75s",
            }
            else "assertion_or_runtime")
        return result
    except (OSError, UnicodeError, ValueError, RecursionError):
        return {}


def _native_abort_diagnosis(source: Path, scenario: str, code: int) -> dict:
    """Classify a native abort using fixed markers, never disclose log text.

    This is a hint from stderr plus the actual process exit, not an assertion
    of root cause. Native aborts may leave no Python report at all.
    """
    if code not in {-6, 134}:
        return {}
    result = {"native_failure_kind": "unclassified_native_abort"}
    directory = source.resolve().parent / scenario
    nested = directory / scenario
    log = nested / "stderr.log"
    try:
        if any(p.is_symlink() for p in (directory, nested, log)):
            return result
        if not log.is_file() or log.stat().st_size > MAX_BYTES:
            return result
        with log.open("rb") as handle:
            handle.seek(max(0, log.stat().st_size - 32768))
            tail = handle.read(32768)
        markers = (
            ("allocator_abort_marker", (b"malloc():", b"double free or corruption",
                                        b"free(): invalid pointer", b"corrupted size vs.")),
            ("gtk_assertion_marker", (b"Gtk:ERROR:", b"Gtk-ERROR **:")),
            ("glib_assertion_marker", (b"GLib:ERROR:", b"GLib-GObject:ERROR:")),
            ("python_abort_marker", (b"Fatal Python error:",)),
        )
        for category, candidates in markers:
            if any(marker in tail for marker in candidates):
                result["native_failure_kind"] = category
                break
    except OSError:
        pass
    return result


def safe_summary(source: Path) -> dict:
    # Fail closed for symlinks, invalid JSON, unreasonable sizes or fields.
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise ValueError("invalid E2E input")
    data = json.loads(source.read_bytes().decode("utf-8"))
    if not isinstance(data, list) or len(data) > MAX_ROWS:
        raise ValueError("invalid E2E result list")
    allowed = _allowed_scenarios()
    rows = []
    for item in data:
        scenario = item.get("scenario") if isinstance(item, dict) else None
        if type(scenario) is not str or scenario not in allowed:
            raise ValueError("unexpected E2E scenario")
        code = item.get("returncode")
        if type(code) is not int or not -255 <= code <= 255:
            raise ValueError("invalid E2E return code")
        row = {
            "scenario": scenario,
            "status": "PASS" if code == 0 else "FAIL",
            "returncode": code,
        }
        ratio = item.get("visual_change_ratio")
        if ratio is not None:
            if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
                raise ValueError("invalid E2E visual ratio")
            row["visual_change_ratio"] = round(ratio, 6)
        # Deliberately discard every other field, including raw assertion
        # messages, personal paths, URLs, JSON/JSONL, logs, pixels and images.
        if code != 0:
            row.update(_failed_diagnostic(source, scenario))
            row.update(_native_abort_diagnosis(source, scenario, code))
        rows.append(row)
    return {"schema": "cifereca-e2e-public-verdict/v1", "results": rows}


def export(source: Path, output_directory: Path) -> Path:
    payload = safe_summary(source)
    # Exclusive creation avoids reusing an attacker-controlled or stale file.
    output_directory.mkdir(parents=True, exist_ok=False)
    target = output_directory / "summary.json"
    with target.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
        handle.write("\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export privacy-safe E2E verdicts only")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        export(args.source, args.out_dir)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        # Never echo arbitrary exception text: it can include untrusted data.
        print("E2E publication refused (invalid or untrusted evidence)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
