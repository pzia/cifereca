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
    "P0_1347_kobo_audit_pointer_dispatch",
    "P0_1347_kobo_audit_result_wait",
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
        "diagnostics-raw-export-1366": "diagnostics_raw_export_probe.py",
        "diagnostics-large-1600": "diagnostics_page_probe.py",
        "diagnostics-states-1366": "diagnostics_states_probe.py",
        "diagnostics-structured-1366": "diagnostics_structured_probe.py",
        "diagnostics-volume-1366": "diagnostics_volume_probe.py",
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
        # A Diagnostics wait can poll a bound GTK method as well as a lambda.
        # Publish only an AST-reviewed `until(...)` call-site line, never a
        # predicate name, user data, GTK repr, status text or traceback.
        if probes[scenario] in {"diagnostics_page_probe.py", "diagnostics_states_probe.py", "diagnostics_structured_probe.py", "diagnostics_volume_probe.py", "diagnostics_raw_export_probe.py"}:
            wait_line = data.get("wait_line")
            allowed_waits = {n.lineno for n in ast.walk(probe)
                             if isinstance(n, ast.Call)
                             and isinstance(n.func, ast.Name)
                             and n.func.id == "until"}
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
        # #2095: only closed, fixture-derived diagnostic state is publishable
        # for the failed *real* MainWindow audit acceptance. In particular,
        # the raw subtitle, source ID, pointers and task exceptions never pass.
        observation = data.get("kobo_audit_observation")
        if observation is not None and scenario in {
            "mainwindow-baseline-1366", "mainwindow-baseline-1600",
        }:
            if (type(observation) is not dict
                    or set(observation) != {
                        "clicked_signal", "broker_calls", "subtitle_phase",
                        "row_recreated", "preview_generation_changed",
                        "identity_generation_changed", "page_rebuilt",
                    }
                    or type(observation["clicked_signal"]) is not bool
                    or type(observation["broker_calls"]) is not int
                    or not 0 <= observation["broker_calls"] <= 2
                    or any(type(observation[key]) is not bool for key in (
                        "row_recreated", "preview_generation_changed",
                        "identity_generation_changed", "page_rebuilt",
                    ))
                    or type(observation["subtitle_phase"]) is not str
                    or observation["subtitle_phase"] not in {
                        "initial", "in_progress", "other",
                    }):
                raise ValueError("untrusted Kobo audit E2E diagnostic")
            result["kobo_audit_observation"] = {
                key: observation[key] for key in (
                    "clicked_signal", "broker_calls", "subtitle_phase",
                    "row_recreated", "preview_generation_changed",
                    "identity_generation_changed", "page_rebuilt",
                )
            }
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


def _failed_maintenance_checkpoint(source: Path, scenario: str) -> dict:
    """Share *only* a constant reached step from the synthetic GTK recipe.

    Raw screenshots, widget text, exception messages and private credentials
    remain in the private CI workspace and are never published.
    """
    if scenario not in {
        # Identical fully synthetic probe: allow narrow layouts to publish
        # the same closed milestone without exposing a screenshot, assertion,
        # path, widget text, geometry or private traceback.
        "maintenance-tools-920", "maintenance-tools-1024",
        "maintenance-tools-1280", "maintenance-tools-1366",
        "maintenance-tools-1600", "maintenance-tools-1728",
    }:
        return {}
    root = source.resolve().parent / scenario
    nested = root / scenario
    report = nested / "screen.json"
    try:
        if any(path.is_symlink() for path in (root, nested, report)):
            return {}
        if not report.is_file() or report.stat().st_size > MAX_BYTES:
            return {}
        data = json.loads(report.read_text(encoding="utf-8"))
        if type(data) is not dict or type(data.get("checks")) is not dict:
            return {}
        checks = data["checks"]
        # No dynamic or unreviewed values: output is one literal enum.
        milestones = (
            ("real_tools_route_and_action", "maintenance_route"),
            ("P1_1498-duplicates_cancelled_terminal_on_own_line", "maintenance_p1_done"),
            ("inspection_setup_entered", "inspection_setup"),
            ("inspection_fixture_created", "inspection_fixture"),
            ("inspection_host_mapped", "inspection_host"),
            ("inspection_click_dispatched", "inspection_clicked"),
            ("inspection_window_mapped", "inspection_open"),
            ("P0_2712_initial_window_width_ok", "inspection_width_ok"),
            ("P0_2712_initial_window_height_ok", "inspection_height_ok"),
            ("P0_2712_initial_header_ok", "inspection_header_ok"),
            ("P0_2712_initial_scroller_child_ok", "inspection_scroller_child_ok"),
            ("P0_2712_main_scroll_present", "inspection_scroll_ok"),
            ("local_inspection_provider_filter_does_not_attribute_isbn_files", "numilog_filtered"),
            ("inspection_candidates_initially_visible", "candidate_first"),
            ("candidate_heading_verified", "candidate_heading"),
            ("candidate_count_verified", "candidate_count"),
            ("candidate_label_verified", "candidate_label"),
            ("candidate_button_found", "candidate_button"),
            ("candidate_button_click_ready", "candidate_button_ready"),
            ("candidate_button_clicked", "candidate_button_clicked"),
            ("inspection_candidate_navigation_reached", "candidate_next"),
            ("local_inspection_right_candidate_side_by_side_no_attestation", "candidate_validated"),
            ("native_manual_toggle_lookup_started", "manual_toggle_search"),
            ("native_manual_toggle_found", "manual_toggle_found"),
            ("native_manual_toggle_pointer_hit", "manual_toggle_hit"),
            ("native_manual_toggle_clicked", "manual_toggle_clicked"),
            ("native_manual_toggle_result_visible", "manual_toggle_result"),
            ("native_manual_panel_labels_validated", "manual_panel_labels"),
            ("native_manual_list_has_25_rows", "manual_list_rows"),
            ("native_manual_file_row_hit_tested", "manual_file_row_hit"),
            ("native_manual_file_row_clicked", "manual_file_row_clicked"),
            ("local_inspection_manual_unlinked_filter_same_identity", "manual_filtered"),
            ("local_inspection_manual_does_not_change_candidate_set", "manual_restored"),
            ("local_inspection_state_pagination_and_private_proof", "inspection_paginated"),
            ("local_inspection_epoch_revokes_window_and_sources_unchanged", "inspection_complete"),
            ("native_declaration_started", "declaration_started"),
            ("native_declaration_audit_done", "declaration_audit"),
            ("native_declaration_dialog_open", "declaration_dialog"),
            ("native_declaration_right_row_found", "declaration_right_row"),
            ("native_declaration_right_row_clicked", "declaration_right_clicked"),
            ("native_declaration_right_is_selected", "declaration_right_selected"),
            ("native_declaration_manual_files_visible", "declaration_manual_files"),
            ("native_declaration_one_file_available", "declaration_one_file"),
            ("native_declaration_core_pair_admissible", "declaration_core_pair"),
            ("native_declaration_right_selected", "declaration_right"),
            ("native_declaration_expander_open", "declaration_expander"),
            ("native_declaration_preview_dialog", "declaration_preview"),
            ("native_declaration_committed", "declaration_committed"),
            ("native_declaration_revoked", "declaration_revoked"),
        )
        reached = "before_maintenance_route"
        for field, label in milestones:
            if checks.get(field) is True:
                reached = label
        return {"maintenance_last_safe_checkpoint": reached}
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
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
        # #2027: these three *closed* machine fields are safe to publish;
        # any additional key or non-native number is rejected fail-closed.
        # Neither the GTK diagnostic line nor any widget pointer is exported.
        counters = item.get("gtk_layout_critical")
        if counters is not None:
            if (type(counters) is not dict
                    or set(counters) != {"GtkStack", "GtkRevealer", "stream_truncated"}
                    or type(counters["GtkStack"]) is not int
                    or type(counters["GtkRevealer"]) is not int
                    or type(counters["stream_truncated"]) is not bool
                    or not 0 <= counters["GtkStack"] <= 100000
                    or not 0 <= counters["GtkRevealer"] <= 100000):
                raise ValueError("invalid GTK geometry counters")
            row["gtk_layout_critical"] = counters
        # P0 #2027: phase labels and widget classes are reviewed literal
        # constants, not GTK stderr, paths or dynamic provider metadata.
        phases = item.get("gtk_layout_phase")
        if phases is not None:
            allowed_phases = frozenset({
                "window_start", "window_shown", "sync_desktop",
                "sync_font_desktop", "sync_narrow", "sync_font_narrow",
                "sync_restored", "numilog_consent", "done",
                "maint_window_start", "maint_window_shown", "maint_route",
                "maint_busy_guard", "maint_scan", "maint_first_dialog",
                "maint_cancel_stale", "maint_execute", "maint_refresh",
                "maint_p1_naming", "maint_p1_duplicates", "maint_done",
                "maint_dialog_visible", "maint_dialog_response",
                "maint_p1_dialog_visible", "maint_p1_dialog_response",
            })
            if (type(phases) is not dict or len(phases) > len(allowed_phases)
                    or not set(phases).issubset(allowed_phases)):
                raise ValueError("invalid GTK phase labels")
            for counts in phases.values():
                if (type(counts) is not dict
                        or set(counts) != {"GtkStack", "GtkRevealer"}
                        or any(type(value) is not int or not 0 <= value <= 100000
                               for value in counts.values())):
                    raise ValueError("invalid GTK phase counts")
            if phases:
                row["gtk_layout_phase"] = phases
        # Deliberately discard every other field, including raw assertion
        # messages, personal paths, URLs, JSON/JSONL, logs, pixels and images.
        if code != 0:
            row.update(_failed_diagnostic(source, scenario))
            row.update(_failed_maintenance_checkpoint(source, scenario))
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
