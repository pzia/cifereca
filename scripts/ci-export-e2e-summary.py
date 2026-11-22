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
        # The bulk-refresh timeout exposes only fixture and widget booleans.
        # Never publish journal rows, labels, timing, errors or broker state.
        observation = data.get("bulk_refresh_observation")
        if (scenario == "diagnostics-large-1600"
                and stage == "P0_2140_bulk_fixture"
                and type(observation) is dict
                and set(observation) == {
                    "refresh_mapped", "refresh_sensitive", "fixture_enabled",
                    "broker_queried",
                }
                and all(type(value) is bool for value in observation.values())):
            result["bulk_refresh_observation"] = {
                key: observation[key] for key in (
                    "refresh_mapped", "refresh_sensitive", "fixture_enabled",
                    "broker_queried",
                )
            }
        # #5376: a bulk button may fail the 4s real GTK focus retry,
        # independently of the preceding refresh. Exact boolean-only data.
        focus = data.get("bulk_focus_observation")
        if (scenario == "diagnostics-large-1600"
                and stage == "P0_2140_bulk_fixture"
                and type(focus) is dict
                and set(focus) == {
                    "button_mapped", "button_sensitive", "toplevel_active",
                    "button_focused", "toplevel_focus_is_button",
                }
                and all(type(value) is bool for value in focus.values())):
            result["bulk_focus_observation"] = {
                key: focus[key] for key in (
                    "button_mapped", "button_sensitive", "toplevel_active",
                    "button_focused", "toplevel_focus_is_button",
                )
            }
        chooser = data.get("capture_file_selection_timeout")
        if (scenario == "diagnostics-1366"
                and type(chooser) is dict
                and set(chooser) == {
                    "dialog_visible", "selected_file_present",
                    "selected_file_matches_fixture",
                    "current_name_matches_fixture",
                }
                and all(type(value) is bool for value in chooser.values())):
            result["capture_file_selection_timeout"] = {
                key: chooser[key] for key in (
                    "dialog_visible", "selected_file_present",
                    "selected_file_matches_fixture",
                    "current_name_matches_fixture",
                )
            }
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



# The only public book-detail diagnostic values are reviewed fixed callback
# identifiers; a supplier title, DOM text, error or Python exception cannot
# be uploaded even when the raw E2E report contains it.
_BOOK_DETAIL_STAGES = frozenset({
    "activation",
    "callback_other",
    "search_initial",
    "wait_search",
    "search_detail",
    "expanded_search",
    "verify_tab_order",
    "click_copy",
    "read_clipboard",
    "search_isbn",
    "click_isbn",
    "verify_isbn",
    "library_select",
    "verify_library",
    "finish",
    "reset_library_detail_for_security_audit",
    "security_detail_expanders",
    "audit_mixed_security_values",
    "audit_mixed_security_values_rendered",
    "audit_all_private_values",
    "audit_all_private_values_rendered",
    "begin_stale_public_detail",
    "stale_public_detail_rendered",
    "component_summaries",
    "component_summary_short",
    "component_summary_six_lines",
    "component_summary_seven_lines",
    "component_expand",
    "component_collapse",
    "component_click_collapse",
    "component_overflow",
    "component_open_overflow",
    "component_click_hidden",
    "component_lists",
    "component_keyboard_start",
    "component_keyboard_menu",
    "component_keyboard_hidden",
    "component_keyboard_activate",
    "component_keyboard_escape",
    "component_keyboard_close",
    "component_keyboard_done",
    "component_identity",
    "component_identity_rendered",
})


def _failed_book_detail_checkpoint(source: Path, scenario: str) -> dict:
    """Expose the last *closed* GUI callback stage, never raw failure data."""
    if scenario not in {
        "book-detail-960", "book-detail-1280", "book-detail-1300",
        "book-detail-1366", "book-detail-1600", "book-detail-1700",
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
        if type(data) is not dict:
            return {}
        result = {}
        # Fixed phase names from the isolated synthetic C probe. This cannot
        # disclose an arbitrary exception, fixture title, URL or account.
        stage = data.get("local_c_stage")
        local_stages = {"setup", "seed", "complete", "warm_n2_cold_n3",
                        "distinct_c_shared_n3", "invalidated_n2_warm_n3",
                        "epub_priority_cold", "epub_priority_warm",
                         "epub_qualified_fallback", "catalogue_http_fallback"} | {
            prefix + suffix
            for prefix in ("cold_", "offline_")
            for suffix in ("project", "library_route", "library_list",
                           "library_detail", "recent_route", "recent_list",
                           "recent_detail", "search_route", "search_list", "search_detail",
                           "loans_route", "loans_list", "loans_detail",
                           "history_route", "history_list", "history_detail",
                           "recommendations_route", "recommendations_list", "recommendations_detail")
        }
        if type(stage) is str and stage in local_stages:
            result["book_detail_local_c_stage"] = stage
        fallback_line = data.get("local_c_fallback_assertion_line")
        if stage == "epub_qualified_fallback" and type(fallback_line) is int:
            tree = ast.parse((ROOT / "e2e/notice_c_local_views_contract.py").read_text(encoding="utf-8"))
            permitted = {
                node.lineno
                for function in ast.walk(tree)
                if isinstance(function, ast.FunctionDef)
                and function.name in {"verify_qualified_fallback", "fallback_painted"}
                for node in ast.walk(function)
                if isinstance(node, ast.Assert)
            }
            if fallback_line in permitted:
                result["book_detail_local_c_fallback_assertion_line"] = fallback_line
        # Only individually reviewed, synthetic stages from the C-cover helper.
        cover_stage = data.get("notice_c_cover_stage")
        cover_stages = {"tie_setup", "tie_first", "tie_second", "setup", "seed", "complete"} | {
            prefix + suffix for prefix in ("cold_", "offline_")
            for suffix in ("texture", "web", "web_choices", "search_list", "search_detail")
        }
        if type(cover_stage) is str and cover_stage in cover_stages:
            result["book_detail_notice_c_cover_stage"] = cover_stage
        last = data.get("public_stage")
        if type(last) is str and last in _BOOK_DETAIL_STAGES:
            result["book_detail_last_stage"] = last
        # The capture stage is a literal enum, never a Pillow/X11 error,
        # private screenshot, file path or dynamic exception message.
        capture_phase = data.get("capture_failure_phase")
        if type(capture_phase) is str and capture_phase in {
            "imagegrab", "display_geometry", "png_write",
        }:
            result["book_detail_capture_failure_phase"] = capture_phase
        line = data.get("callback_line")
        if type(line) is int:
            tree = ast.parse((ROOT / "e2e/book_detail_probe.py").read_text(encoding="utf-8"))
            permitted = {node.lineno for node in ast.walk(tree)
                         if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Name)
                         and node.func.id == "later"}
            if line in permitted:
                result["book_detail_callback_line"] = line
        return result
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        pass
    return {}


# Closed stages from the synthetic BnF XTEST cover probe. A failed GTK
# verification may publish its last *fixed stage*, never the screenshot,
# widget text, provider data, filesystem path or raw exception.
_BNF_COVER_STAGES = frozenset({
    "startup", "two_distinct_list_covers", "first_detail", "second_detail",
    "missing_placeholder", "search_late_detail_rebind",
    "two_exact_loan_list_covers",
    "first_loan_detail_cover", "second_loan_detail_cover",
    "loan_missing_cover_placeholder",
    "remote_loan_image_without_source_thumbnail",
    "remote_loan_detail_cover", "embedded_loan_list_over_remote",
    "embedded_loan_detail_over_remote",
    "embedded_loan_list_over_supplier", "embedded_loan_detail_over_supplier",
    "late_library_loan_list_cover", "late_library_loan_detail_cover",
    "search_results",
    "global_button_visible", "borrower_c_setup", "borrower_c_seed", "borrower_c_complete",
} | {
    "borrower_c_" + phase + "_" + stage
    for phase in ("cold", "offline")
    for stage in ("project", "publish", "loans_list", "loans_detail",
                  "history_list", "history_detail")
})


def _failed_bnf_cover_checkpoint(source: Path, scenario: str) -> dict:
    if scenario != "bnf-covers-1366":
        return {}
    # This probe saves directly into --out, unlike the nested book-detail
    # report. Do not follow symlinks or export provider-owned arbitrary text.
    # CI invokes e2e/run.py with --out e2e-artifacts/<scenario>;
    # the runner itself appends its scenario name once more.
    outer = source.resolve().parent / scenario
    folder = outer / scenario
    report = folder / "screen.json"
    try:
        if outer.is_symlink() or folder.is_symlink() or report.is_symlink():
            return {}
        if not report.is_file() or report.stat().st_size > MAX_BYTES:
            return {}
        data = json.loads(report.read_text(encoding="utf-8"))
        if type(data) is not dict:
            return {}
        stage = data.get("last_stage")
        if type(stage) is str and stage in _BNF_COVER_STAGES:
            return {"bnf_covers_last_stage": stage}
    except (OSError, ValueError, UnicodeError, RecursionError):
        pass
    return {}


# Only these source-defined synthetic phases may leave the GTK runner.
_PLUGIN_SCROLL_STAGES = frozenset({
    "initial_scroll", "first_toggle", "first_grab_focus",
    "first_scroll_request", "first_toggle_geometry", "first_toggle_scroll",
    "first_toggle_set_inactive", "first_toggle_callback",
    "first_geometry", "first_scroll",
    "first_anchor", "first_switch", "first_focus", "second_geometry",
})


def _failed_plugin_scroll_checkpoint(source: Path, scenario: str) -> dict:
    if scenario not in {"plugin-manager-scroll-1024", "plugin-manager-scroll-1366"}:
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
        if type(data) is not dict:
            return {}
        stage = data.get("plugin_scroll_checkpoint")
        if type(stage) is str and stage in _PLUGIN_SCROLL_STAGES:
            return {"plugin_scroll_last_check": stage}
    except (OSError, UnicodeError, ValueError, RecursionError):
        pass
    return {}


def _failed_maintenance_checkpoint(source: Path, scenario: str) -> dict:
    """Share closed reached steps and a source-validated probe line only.

    Raw screenshots, widget text, exception messages and private credentials
    remain in the private CI workspace and are never published.
    A probe line indicates an assertion or call site, not a root cause.
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
            ("cache_progress_attached_to_real_tool_row", "maintenance_progress_row"),
            ("busy_sync_refuses_preview_before_scan_or_delete", "maintenance_busy_guard"),
            ("analysis_indeterminate_on_tool_row", "maintenance_analysis"),
            ("slow_worker_started_without_mutation", "maintenance_slow_worker"),
            ("gtk_heartbeat_during_slow_scan", "maintenance_heartbeat"),
            ("initial_maintenance_plan_ready", "maintenance_plan_ready"),
            ("initial_maintenance_dialog_mapped", "maintenance_dialog_mapped"),
            ("initial_maintenance_confirmation_visible", "maintenance_confirmation_visible"),
            ("confirmation_on_tool_row_without_percentage", "maintenance_confirmation"),
            ("physical_cancel_never_runs_destructive_tool", "maintenance_cancel"),
            ("stale_plan_rejected_under_lock_without_delete", "maintenance_stale_plan"),
            ("only_explicit_confirmed_safe_simulation_executes", "maintenance_execution"),
            ("refresh_cancels_worker_and_discards_late_progress", "maintenance_refresh"),
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
            # Closed, boolean-only diagnostics for the mandatory attested
            # reconciliation phase following user-declaration revocation.
            ("native_declaration_revoked_verified", "declaration_revoked_verified"),
            ("native_declaration_cleanup_started", "declaration_cleanup_started"),
            ("native_declaration_cleanup_done", "declaration_cleanup_done"),
            ("native_declaration_attested_started", "attested_started"),
            ("native_declaration_attested_fixture_ready", "attested_fixture"),
            ("native_declaration_attested_audit_complete", "attested_audit"),
            ("native_declaration_attested_right_ready", "attested_right"),
            ("native_declaration_attested_dialog_open", "attested_dialog"),
            ("native_declaration_attested_right_selected", "attested_selected"),
            ("native_declaration_attested_file_view_visible", "attested_files"),
            ("native_declaration_attested_preflight_reached", "attested_preflight"),
            ("native_declaration_attested_preview_reached", "attested_preview"),
            ("P0_4859_native_historical_kobo_refusal_no_B", "attested_refusal_confirmed"),
        )
        reached = "before_maintenance_route"
        for field, label in milestones:
            if checks.get(field) is True:
                reached = label
        result = {"maintenance_last_safe_checkpoint": reached}
        # The report contains no public stack text. Only a native integer
        # matching an assertion, raise or call site in this exact, checked-in
        # synthetic probe may cross the export boundary.
        line = data.get("maintenance_failure_line")
        if type(line) is int:
            try:
                probe = ast.parse((ROOT / "e2e/maintenance_tools_probe.py").read_text(
                    encoding="utf-8"))
                allowed_lines = {node.lineno for node in ast.walk(probe)
                                 if isinstance(node, (ast.Assert, ast.Raise, ast.Call))}
                if line in allowed_lines:
                    result["maintenance_failure_line"] = line
            except (OSError, UnicodeError, SyntaxError, ValueError, RecursionError):
                pass
        return result
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return {}


# Fixed, source-reviewed maintenance phase names emitted by the synthetic
# GTK probe to *both* streams. A SIGABRT cannot execute Python cleanup.
_MAINTENANCE_ABORT_PHASES = frozenset({
    "maint_window_start", "maint_window_shown", "maint_route",
    "maint_busy_guard", "maint_scan", "maint_first_dialog",
    "maint_cancel_stale", "maint_execute", "maint_refresh",
    "maint_p1_naming", "maint_p1_duplicates", "maint_done",
    "maint_dialog_visible", "maint_dialog_response",
    "maint_p1_dialog_visible", "maint_p1_dialog_response",
    "maint_inspection_opened", "maint_inspection_right_selected",
    "maint_inspection_candidate_navigated", "maint_inspection_file_selected",
    "maint_inspection_preflight", "maint_inspection_finished",
    "maint_declaration_start", "maint_declaration_audit_done",
    "maint_declaration_dialog_open", "maint_declaration_right_selected",
    "maint_declaration_expander_open", "maint_declaration_preview_dialog",
    "maint_declaration_committed", "maint_declaration_revoked",
    "maint_transfer_start", "maint_transfer_done",
})


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
        # The probe emits a closed stage immediately before each GTK action.
        # Read only exact complete lines from stderr and export one reviewed
        # identifier; never print raw log text or inferred exception details.
        if scenario.startswith("maintenance-tools-"):
            for line in tail.splitlines():
                if line.startswith(b"CIFERECA_E2E_GTK_PHASE:"):
                    phase = line.removeprefix(b"CIFERECA_E2E_GTK_PHASE:")
                    if phase.isascii():
                        name = phase.decode("ascii")
                        if name in _MAINTENANCE_ABORT_PHASES:
                            result["maintenance_native_last_phase"] = name
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


# Numeric receipt for the completed synthetic six-view probe only (#2435).
# Closed metric keys: no prefix acceptance of arbitrary diagnostic labels.
_COVER_ROUTES = frozenset(
    route + "/" + variant
    for route in ("library", "recent", "search", "loans", "history", "recommendations")
    for variant in ("list", "detail")
)
_COVER_METRICS = {
    "n1": frozenset(
        "n1_" + state + "_" + suffix
        for state in ("selected", "pending", "missing", "ambiguous")
        for suffix in ("count", "recent_count", "recent_p50_ms", "recent_p95_ms")
    ),
    "n2": frozenset({
        "n2_hit", "n2_miss", "n2_source_unknown", "n2_resolution_miss",
        "n2_register_new", "n2_register_changed", "n2_register_reused",
        "n2_source_refresh_scheduled", "n2_registered_documents", "n2_source_flights",
    }) | frozenset(
        "n2_" + state + "_" + suffix
        for state in ("hit", "source_unknown", "below_floor", "resolution_miss")
        for suffix in ("recent_count", "recent_p50_ms", "recent_p95_ms")
    ),
    "n3": frozenset({
        "preview_created", "preview_hit", "preview_fallback", "cache_hit_valid",
        "download_ok", "download_failed", "n3_url_published_bytes",
        "n3_epub_hit", "n3_epub_miss", "n3_epub_singleflight",
        "n3_epub_failed", "n3_epub_cooldown", "n3_epub_overflow",
    }) | frozenset(
        "n3_url_" + state + "_" + suffix
        for state in ("ok", "failed")
        for suffix in ("avg_ms", "max_ms", "recent_count", "recent_p50_ms", "recent_p95_ms")
    ) | frozenset(
        "n3_epub_" + state + "_" + suffix
        for state in ("ok", "failed")
        for suffix in ("recent_count", "recent_p50_ms", "recent_p95_ms")
    ) | frozenset(
        "preview_worker_" + state + "_" + suffix
        for state in ("hit", "created", "fallback")
        for suffix in ("recent_count", "recent_p50_ms", "recent_p95_ms")
    ),
    "n4": frozenset({
        "ui_applied", "ui_failed", "ui_stale", "ui_decode_count",
        "ui_decode_slow", "ui_decode_avg_ms", "ui_decode_max_ms",
        "ui_decode_recent_count", "ui_decode_recent_p50_ms", "ui_decode_recent_p95_ms",
    }),
}


def _cover_number(value, *, duration):
    if duration:
        return (type(value) in (int, float) and 0 <= value <= 600000
                and math.isfinite(value))
    return type(value) is int and 0 <= value <= 1000000000


def _completed_cover_metrics(source: Path, scenario: str) -> dict:
    if scenario not in {
        "book-detail-960", "book-detail-1280", "book-detail-1300",
        "book-detail-1366", "book-detail-1600", "book-detail-1700",
    }:
        return {}
    root = source.parent / scenario
    nested = root / scenario
    report = nested / "screen.json"
    try:
        if any(path.is_symlink() for path in (root, nested, report)):
            return {}
        if not report.is_file() or report.stat().st_size > MAX_BYTES:
            return {}
        with report.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return {}
        data = json.loads(raw)
        if (type(data) is not dict or data.get("local_c_stage") != "complete"
                or type(data.get("checks")) is not dict
                or data["checks"].get("P0_4980_same_c_six_physical_views_restart_offline") is not True):
            return {}
        result = {}
        for phase in ("cold", "offline"):
            receipt = data.get("notice_c_cover_metrics_" + phase)
            if (type(receipt) is not dict
                    or receipt.get("schema") != "cifereca.cover-six-views/1"
                    or receipt.get("fixture") != "one_c_six_views_mock_transport"
                    or type(receipt.get("image_gets_total")) is not int
                    or receipt["image_gets_total"] != 1):
                return {}
            timing = receipt.get("route_to_observed_texture_ms")
            if (type(timing) is not dict or set(timing) != _COVER_ROUTES
                    or not all(_cover_number(v, duration=True) for v in timing.values())):
                return {}
            safe = {"image_gets_total": 1,
                    "route_to_observed_texture_ms": {k: round(v, 2) for k, v in timing.items()}}
            for level, keys in _COVER_METRICS.items():
                values = receipt.get(level)
                if type(values) is not dict:
                    return {}
                selected = {}
                for key in keys & values.keys():
                    value = values[key]
                    if not _cover_number(value, duration=key.endswith("_ms")):
                        return {}
                    selected[key] = round(value, 2) if key.endswith("_ms") else value
                safe[level] = selected
            if (safe["n1"].get("n1_selected_count", 0) < 1
                    or safe["n2"].get("n2_hit", 0) < 1
                    or safe["n3"].get("download_ok") != (1 if phase == "cold" else 0)
                    or safe["n3"].get("download_failed") != 0
                    or safe["n4"].get("ui_applied", 0) < 12
                    or safe["n4"].get("ui_failed") != 0
                    or safe["n4"].get("ui_decode_recent_count", 0) < 1):
                return {}
            result[phase] = safe
        evidence = {"notice_c_cover_metrics": {
            "schema": "cifereca.cover-six-views/1",
            "fixture": "one_c_six_views_mock_transport",
            **result,
        }}
        # #4980: this is an explicit assertion in the native GTK fixture,
        # not an inference from an overall PASS or from a generic texture.
        if data["checks"].get("P0_4980_catalogue_http_fallback_real_gtk") is True:
            evidence["notice_c_catalogue_http_fallback"] = True
        if all(data["checks"].get("P0_3104_retained_private_series_real_gtk_" + phase) is True
               for phase in ("cold", "offline")):
            evidence["notice_c_private_series_cold_offline"] = True
        # #3684: export only a closed receipt, not geometry, labels,
        # focus widget names or the private per-route report.
        routes = {"library", "recent", "search", "loans", "history", "recommendations"}
        narrow_complete = True
        for phase in ("cold", "offline"):
            contexts = data.get("notice_c_narrow_context_" + phase)
            if (data["checks"].get("P0_3684_narrow_six_views_" + phase) is not True
                    or type(contexts) is not dict or set(contexts) != routes
                    or not all(type(item) is dict
                               and item.get("returned") is True
                               and item.get("reopened_exact_selection") is True
                               and item.get("native_focus_returned") is True
                               for item in contexts.values())):
                narrow_complete = False
                break
        if narrow_complete:
            evidence["notice_c_narrow_six_views_cold_offline"] = True
        return evidence
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return {}


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
            row.update(_failed_book_detail_checkpoint(source, scenario))
            row.update(_failed_bnf_cover_checkpoint(source, scenario))
            row.update(_failed_plugin_scroll_checkpoint(source, scenario))
            row.update(_failed_maintenance_checkpoint(source, scenario))
            row.update(_native_abort_diagnosis(source, scenario, code))
        else:
            row.update(_completed_cover_metrics(source, scenario))
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
