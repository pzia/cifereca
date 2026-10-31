from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "e2e/run.py"


def _module():
    spec = importlib.util.spec_from_file_location("cifereca_e2e_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_reference_scenario_is_targetable_by_name_and_domain():
    runner = _module()
    assert [name for name, _ in runner.select("startup", None)] == ["startup"]
    assert [name for name, _ in runner.select(None, "core")] == [
        "search-filter-p0", "book-detail-1300", "book-detail-1366", "book-detail-1600",
        "mainwindow-baseline-1366", "mainwindow-baseline-1600",
        "llm-settings-1366", "llm-settings-1600",
        "maintenance-tools-920", "maintenance-tools-1024", "maintenance-tools-1280", "maintenance-tools-1366", "maintenance-tools-1600", "maintenance-tools-1728",
        "startup", "core-navigation", "public-fault-search-1366", "kobo-fault-search-1366", "baen-fault-search-1366",
    ]


def test_1127_real_gtk_llm_consent_cancel_and_lifetime_1366_1600():
    runner = _module()
    probe = (ROOT / "e2e/llm_settings_probe.py").read_text(encoding="utf-8")
    for width in (1366, 1600):
        scenario = runner.SCENARIOS[f"llm-settings-{width}"]
        assert scenario["runner"] == "gtk-llm-settings"
        assert scenario["fixture"] == "startup"
        assert scenario["plugin"] == "core"
        assert scenario["width"] == width
    for evidence in (
        "MainWindow(self, core.config, core.library, core.diagnostics)",
        "device.click_widget(button)",
        "remote_consent_cancelled",
        "reset_drops_late_reply_ui_responsive",
        "pending_test_preserves_keyboard_focus",
        "key_value_not_rendered_or_persisted",
        "saved_requires_restart_without_http",
        "synthetic_only",
        "report['status'] = 'PASS'",
    ):
        assert evidence in probe


def test_1358_maintenance_safety_uses_real_GTK_XTEST_without_destructive_IO():
    runner = _module()
    source = (ROOT / "e2e/maintenance_tools_probe.py").read_text(encoding="utf-8")
    for width in (920, 1024, 1280, 1366, 1600, 1728):
        scenario = runner.SCENARIOS[f"maintenance-tools-{width}"]
        assert scenario["plugin"] == "core"
        assert scenario["fixture"] == "startup"
        assert scenario["runner"] == "gtk-maintenance"
        assert scenario["width"] == width
        if width == 920:
            assert scenario["height"] == 620
        elif width in (1024, 1366):
            assert scenario["height"] == 768
        elif width == 1280:
            assert scenario["height"] == 800
        elif width == 1728:
            assert scenario["height"] == 895
    for marker in (
        'w.navigate("tools")',
        'device[0].click_widget(button)',
        "busy_sync_refuses_preview_before_scan_or_delete",
        "P0_2712_window_fits_real_display",
        "real_multiline_confirmation_after_async_scan",
        "physical_cancel_never_runs_destructive_tool",
        "stale_plan_rejected_under_lock_without_delete",
        "only_explicit_confirmed_safe_simulation_executes",
        'report["status"] = "PASS"',
    ):
        assert marker in source
    inspection_acceptance = (
        ROOT / "e2e/library_inspection_acceptance.py"
    ).read_text(encoding="utf-8")
    assert "P0_2712_initial_window_height_ok" in inspection_acceptance
    assert "assert window.get_height()<=parent.get_height()" in inspection_acceptance


def test_942_shared_book_detail_uses_real_gtk_and_offline_copy_search():
    runner = _module()
    for width in (1366, 1600):
        scenario = runner.SCENARIOS[f"book-detail-{width}"]
        assert scenario["plugin"] == "core"
        assert scenario["fixture"] == "search-filters"
        assert scenario["runner"] == "gtk-detail"
        assert scenario["width"] == width
    assert [n for n, _ in runner.select(None, "core")][:4] == [
        "search-filter-p0", "book-detail-1300", "book-detail-1366", "book-detail-1600",
    ]
    source = (ROOT / "e2e/book_detail_probe.py").read_text(encoding="utf-8")
    for marker in (
        "MainWindow(self, core.config, core.library, core.diagnostics)",
        "core.storage.catalog_entry_put(entry)",
        'expander.set_expanded(True)',
        'device.click_widget(button)',
        'clipboard.read_text_async(None, done)',
        '"online": True, "identity_id": "camille"',
        'window.navigate("library")',
        'report["status"] = "PASS"',
    ):
        assert marker in source


def test_diagnostics_structured_real_gtk_redacts_and_preserves_types():
    runner = _module()
    scenario = runner.SCENARIOS["diagnostics-structured-1366"]
    assert scenario["runner"] == "gtk-diagnostics-structured"
    assert scenario["fixture"] == "startup"
    assert scenario["width"] == 1366
    source = (ROOT / "e2e/diagnostics_structured_probe.py").read_text(encoding="utf-8")
    for marker in (
        "D07_readable_preserves_false_zero_null_empty_and_unicode",
        "D07_json_indented_typed_and_redacted",
        "D07_json_file_export_exact_redacted_via_gtk",
        "D07_real_gtk_file_chooser_opened_by_xtest",
        "02c-dialogue-enregistrer-json-gtk",
        "D07_raw_matches_json_without_secret",
        "D13_ctrl_c_selection_expurgated",
        "D13_support_copy_button_xtest_exact_redacted",
        "D13_support_partial_selection_ctrl_c_xtest",
        "D13_report_provenance_visible_in_copy",
        'physical("space")',
        '"Provenance : Core local"',
        'content != SUPPORT_REPORT',
        'physical("c")',
        'device[0].key("Control_L", True)',
        'get_has_selection()',
        'json.loads(body)',
        'report["unmet_criteria"]',
    ):
        assert marker in source


def test_diagnostics_volume_real_gtk_bounded_broker_and_provenance():
    runner = _module()
    scenario = runner.SCENARIOS["diagnostics-volume-1366"]
    assert scenario["runner"] == "gtk-diagnostics-volume"
    assert scenario["width"] == 1366
    assert scenario["plugin"] == "ui.page.diagnostics"
    source = (ROOT / "e2e/diagnostics_volume_probe.py").read_text(encoding="utf-8")
    for marker in (
        "TOTAL = 10000",
        "D06_10000_events_50_rows_bounded",
        "D06_stable_pagination_next_50",
        "D09_period_changes_broker_query",
        "D10_adept_code10_no_invented_adobe_cause",
        "D11_paris_return_requested_not_confirmed",
        "D09_event_provenance_source_module_reference_visible",
        "D09_source_filter_no_cross_source_attribution",
        "D08_real_detail_panel_available_and_redacted",
        "D08_long_detail_keyboard_end_xtest",
        "D08_long_path_text_end_to_end_consultable",
        'device[0].key("Control_L", True)',
        'device[0].key("End", True)',
        '"04c-kobo-detail-bottom-xtest"',
        "D12_copy_one_identity_without_mixing_profiles",
        "D12_broker_link_between_profiles_proven",
        "D12_link_proven_by_broker",
        'report["unmet_criteria"]',
    ):
        assert marker in source


def test_diagnostics_states_real_gtk_and_slow_broker_without_network():
    runner = _module()
    state = runner.SCENARIOS["diagnostics-states-1366"]
    assert state["runner"] == "gtk-diagnostics-states"
    assert state["fixture"] == "startup"
    assert state["plugin"] == "ui.page.diagnostics"
    assert state["width"] == 1366
    probe = (ROOT / "e2e/diagnostics_states_probe.py").read_text(encoding="utf-8")
    for marker in (
        "D01_no_sources_not_healthy",
        "D02_zero_events_vs_source_available",
        "D03_error_contextualized_and_ui_responsive",
        "D03_explicit_retry_recovers",
        "D04_active_check_does_not_block_GTK",
        "D04_finished_and_button_reenabled",
        "D14_three_distinct_sections_visible",
        "D14_ai_absent_and_explicit_send_disabled",
        "D14_support_active_and_ai_states_independent_during_check",
        "D14_active_result_not_confused_with_ai",
        "D14_support_report_freshness_explicit",
        "D16_1366_display_narrow_no_horizontal_overflow",
        "D16_narrow_actions_keyboard_reachable",
        "D16_restore_without_losing_diagnostics_state",
        "D16_full_multi_monitor_coverage",

        "D02_explicit_zero_anomaly_with_period",
        "D04_full_mainwindow_navigation",
        "diagnostics_states_probe",
    ):
        if marker != "diagnostics_states_probe":
            assert marker in probe
        else:
            assert "gtk-diagnostics-states" in RUNNER.read_text(encoding="utf-8")
    assert 'Thread(target=worker' in probe
    assert 'device[0].click_widget(check)' in probe
    assert 'report["unmet_criteria"]' in probe


def test_diagnostics_1366_physical_keyboard_module_filters():
    runner = _module()
    scenario = runner.SCENARIOS["diagnostics-1366"]
    assert scenario["runner"] == "gtk-diagnostics"
    assert scenario["width"] == 1366
    source = (ROOT / "e2e/diagnostics_page_probe.py").read_text(encoding="utf-8")
    for expected in (
        "D15_typing_does_not_implicitly_query",
        "D15_tab_and_shift_tab_between_filters_xtest",
        "D15_enter_applies_filter_and_resets_pagination",
        "D15_ctrl_a_delete_return_restores_results",
        'device[0].type_text("paris")',
        'press("Return")',
        'press("BackSpace")',
        'device[0].key("Control_L", True)',
        'broker.event_queries[-1]["offset"] == 0',
    ):
        assert expected in source


def test_diagnostics_large_1600_reuses_real_probe_and_tests_keyboard_scroll():
    runner = _module()
    scenario = runner.SCENARIOS["diagnostics-large-1600"]
    assert scenario["runner"] == "gtk-diagnostics"
    assert scenario["width"] == 1600
    assert scenario["plugin"] == "ui.page.diagnostics"
    source = (ROOT / "e2e/diagnostics_page_probe.py").read_text(encoding="utf-8")
    for marker in (
        "independent_scroll_to_report_end",
        "long_redacted_report_1600",
        "tab_shift_tab_and_space_xtest",
        "D05_report_reduced_with_selection_preserved",
        "D15_return_xtest_activates_button_and_keeps_focus",
        "D05_reexpand_preserves_focus_and_selection",
        "D05_xtest_wheel_keeps_outer_page_fixed",
        "D05_xtest_wheel_at_inner_edge_scrolls_outer_page",
        "09bb-report-wheel-edge-propagates-1600",
        "D13_support_textview_ctrl_c_exact_selection",
        'XTestFakeButtonEvent(device[0].display, 5, True, 0)',
        'device[0].key("Control_L", True)',
        "D13_support_partial_selection_ctrl_c_xtest",
        "D13_support_full_copy_button_xtest",
        "D13_copy_selected_vs_full_distinct",
        'buf.select_range(start_iter, end_iter)',
        'press("c")',
        "D05_keyboard_collapse_focus_and_selection",
        "D05_focus_after_collapse",
        "D05_text_selection_after_collapse",
        'press("Return")',
        'press("Tab")',
        'press("space")',
        "outer_before",
    ):
        assert marker in source


def test_navigation_waits_for_real_gtk_crossfade_before_screenshots():
    source = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text(
        encoding="utf-8"
    )
    assert 'get_property("transition-running")' in source
    assert 'if visible and not fading:' in source
    assert 'later(120, callback)' in source


def test_d04_slow_check_real_mainwindow_navigation_contract():
    runner = _module()
    scenario = runner.SCENARIOS["diagnostics-navigation-slow-1366"]
    assert scenario["runner"] == "gtk-diagnostics-navigation"
    assert scenario["width"] == 1366
    assert scenario["slow_check"] is True
    script = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text(
        encoding="utf-8"
    )
    for assertion in (
        "D04_explicit_async_check_started",
        "D04_physical_library_navigation_during_pending_check",
        "D04_async_check_completes_offscreen",
        "D04_check_result_visible_on_return",
        "SlowNavigationBroker(Broker)",
        'device[0].click_widget(buttons[0])',
        "StateTasks() if args.slow_check",
    ):
        assert assertion in script


def test_diagnostics_navigation_targets_real_mainwindow_and_pointer_events():
    runner = _module()
    scenario = runner.SCENARIOS["diagnostics-navigation-1366"]
    assert scenario["runner"] == "gtk-diagnostics-navigation"
    assert scenario["fixture"] == "startup"
    assert scenario["plugin"] == "ui.page.diagnostics"
    assert scenario["width"] == 1366
    source = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text(encoding="utf-8")
    for marker in (
        "MainWindow(self, core.config, core.library, core.diagnostics)",
        "Pages().pages()",
        "self.diagnostics_page.pages()",
        'click_widget(w.nav_rows["library"])',
        'click_widget(w.nav_rows["diagnostics"])',
        "Configuration / Technique",
        "physical_return_to_library",
        'report["status"] = "PASS"',
    ):
        assert marker in source


def test_942_security_boundary_is_audited_on_existing_real_gtk_1366_scenario():
    """No new CI job/scenario: final public/private detail sink uses real GTK."""
    runner = _module()
    scenario = runner.SCENARIOS["book-detail-1366"]
    assert scenario["width"] == 1366
    assert scenario["runner"] == "gtk-detail"
    source = (ROOT / "e2e/book_detail_probe.py").read_text(encoding="utf-8")
    for marker in (
        "def audit_mixed_security_values():",
        "def audit_mixed_security_values_rendered():",
        "def audit_all_private_values():",
        "def audit_all_private_values_rendered():",
        '"04-library-detail-mixed-safety-gtk"',
        '"05-library-detail-all-private-gtk"',
        '"security_detail_mixed_942"',
        '"security_detail_all_private_942"',
        '"announced_subtitle"',
        '"expander_count"',
        'assert len(report["online_calls"]) == 1',
    ):
        assert marker in source
    assert '"book-detail-1024"' not in RUNNER.read_text(encoding="utf-8")


def test_reference_scenario_runs_real_cifereca_without_plugins():
    runner = _module()
    scenario = runner.SCENARIOS["startup"]
    assert scenario["args"] == ["--no-plugins"]
    assert scenario["plugin"] == "core"


def test_capture_waits_for_visible_surface_instead_of_fixed_startup_sleep():
    source = (
        ROOT / "environnements/openai-chat-debian/capture_app.py"
    ).read_text(encoding="utf-8")
    assert "--startup-timeout" in source
    assert "GTK application did not expose a non-blank surface before timeout." in source


def test_reference_scenario_declares_versioned_fixture():
    runner = _module()
    scenario = runner.SCENARIOS["startup"]
    assert scenario["fixture"] == "startup"
    fixture = ROOT / "e2e/fixtures/startup/config.json"
    assert fixture.is_file()


def test_fixture_materialization_isolated_and_deterministic(tmp_path):
    runner = _module()
    env = runner.materialize_fixture("startup", tmp_path)
    config = Path(env["XDG_CONFIG_HOME"]) / "cifereca/config.json"
    first = config.read_text(encoding="utf-8")
    assert str(tmp_path / "home/EBooks/e2e") in first
    assert "${E2E_LIBRARY}" not in first
    env2 = runner.materialize_fixture("startup", tmp_path)
    assert env2 == env
    assert config.read_text(encoding="utf-8") == first


def test_navigation_scenario_stops_settling_on_visual_change():
    runner = _module()
    args = runner.SCENARIOS["core-navigation"]["capture_args"]
    assert "--settle-after-actions" in args
    assert "--wait-change-ratio" in args
    assert args[args.index("--wait-change-ratio") + 1] == "0.05"


def test_capture_teardown_has_short_grace_before_kill():
    source = (
        ROOT / "environnements/openai-chat-debian/capture_app.py"
    ).read_text(encoding="utf-8")
    assert "process.wait(timeout=0.5)" in source
    assert "process.kill()" in source
    assert "process.wait(timeout=2)" not in source


def test_capture_adaptive_settle_reuses_visual_change_metric():
    source = (
        ROOT / "environnements/openai-chat-debian/capture_app.py"
    ).read_text(encoding="utf-8")
    assert "def visual_change_ratio(" in source
    assert 'ratio >= args.wait_change_ratio' in source
    assert '"settle_after_actions_elapsed_seconds"' in source


def test_search_filter_p0_runs_real_gtk_and_x11_events():
    runner = _module()
    scenario = runner.SCENARIOS["search-filter-p0"]
    assert scenario["runner"] == "gtk-probe"
    assert scenario["fixture"] == "search-filters"
    assert scenario["plugin"] == "core"
    source = (ROOT / "e2e/search_filter_probe.py").read_text(encoding="utf-8")
    assert 'MainWindow(self, core.config, core.library, core.diagnostics)' in source
    assert 'device.click_widget(controls.entry)' in source
    assert 'device.type_text("Camille")' in source
    assert 'device.click_widget(widget)' in source
    for field in ("query", "rows", "count", "selected", "scroll", "search_requests"):
        assert f'"{field}"' in source


def test_search_filter_p0_has_two_identities_and_offline_catalogue():
    import json
    payload = json.loads(
        (ROOT / "e2e/fixtures/search-filters/config.json").read_text(encoding="utf-8")
    )
    assert [row["id"] for row in payload["identities"]] == ["camille", "alex"]
    runner = _module()
    assert runner.SCENARIOS["search-filter-p0"]["runner"] == "gtk-probe"
    assert 'core.storage.catalog_entry_put({' in (
        ROOT / "e2e/search_filter_probe.py"
    ).read_text(encoding="utf-8")


def test_xvfb_display_retry_is_limited_to_transport_initialization():
    runner = _module()
    error = (
        "Gtk-CRITICAL gtk_icon_theme_get_for_display GDK_IS_DISPLAY failed\n"
        "RuntimeError: Gtk couldn't be initialized."
    )
    assert runner.retryable_x11_startup_failure(1, error) is True
    assert runner.retryable_x11_startup_failure(0, error) is False
    assert runner.retryable_x11_startup_failure(1, "AssertionError: expected search page") is False
    assert runner.retryable_x11_startup_failure(
        1, "RuntimeError: Gtk couldn't be initialized. Missing config."
    ) is False
    source = RUNNER.read_text(encoding="utf-8")
    assert 'name in {"startup", "core-navigation"}' in source
    assert '"xvfb_display_retry"' in source
    assert "attempt=2" in source

def test_e2e_workflow_replays_each_selected_scenario_only_once():
    """Replay uses the deduplicated E2E selector, not the full core plugin."""
    runner = _module()
    assert [name for name, _ in runner.select(None, "core")].count(
        "search-filter-p0"
    ) == 1
    workflow = (ROOT / ".github/workflows/e2e-smoke.yml").read_text(
        encoding="utf-8"
    )
    assert 'e2e/run.py --scenario "$scenario"' in workflow
    assert "e2e/run.py --plugin core" not in workflow
    assert "steps.select.outputs.scenarios" in workflow


def test_1599_constellations_real_gtk_navigation_and_identity_contract():
    runner = _module()
    probe = (ROOT / "e2e/constellations_detail_probe.py").read_text(
        encoding="utf-8"
    )
    for width in (1366, 1600):
        scenario = runner.SCENARIOS[f"constellations-detail-{width}"]
        assert scenario["runner"] == "gtk-constellations"
        assert scenario["plugin"] == "ui.page.constellations"
        assert scenario["width"] == width
    for marker in (
        "ConstellationsPlugin().activate(Context())",
        "host = _UiHost(window",
        "PublicBookMasterDetailView",
        "key_activate(neighbour_button(view, \"Livre C\"))",
        "key_activate(view.back_button)",
        "key_activate(view.origin_button)",
        'state["old_button"].emit("clicked")',
        'report["checks"]["stale_neighbour_after_identity_change"]',
        'report["checks"]["stale_neighbour_after_plugin_deactivation"]',
        'report["status"] = "PASS"',
    ):
        assert marker in probe


def test_1599_recommendations_real_gtk_identity_and_local_isbn_action():
    runner = _module()
    probe = (ROOT / "e2e/recommendations_detail_probe.py").read_text(
        encoding="utf-8"
    )
    for width in (1366, 1600):
        scenario = runner.SCENARIOS[f"recommendations-detail-{width}"]
        assert scenario["runner"] == "gtk-recommendations"
        assert scenario["plugin"] == "ui.page.recommendations"
        assert scenario["width"] == width
    for marker in (
        "RecommendationsPlugin().activate(",
        "host = _UiHost(window)",
        "PublicBookMasterDetailView",
        "view._master._page.selection.set_selected(0)",
        "activate_key(action)",
        'view._text.set_text("ABSENT-THIS-TITLE")',
        "view._identity.set_selected(1)",
        'state["old_action"].emit("clicked")',
        'report["checks"]["identity_switch_discards_old_detail"]',
        'report["status"] = "PASS"',
    ):
        assert marker in probe


def test_2027_numilog_consent_e2e_failures_have_stage_and_geometry_evidence():
    """Keep the real GTK assertions; failures must identify their exact gate."""
    import ast

    probe = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text(
        encoding="utf-8"
    )
    ast.parse(probe)
    for marker in (
        'report["stage"] = "P1_1889_sync_760_post_capture_" + key',
        'report["sync_760_post_capture"] = side_effects',
        'report["stage"] = "P0_1905_numilog_inventory_preflight"',
        'report["numilog_inventory_preflight"] = preflight',
        'assert len(inventory_rows) == 1, (',
        'assert preflight["manual_subtitle"], (',
        'assert len(buttons) == 1, (',
        '"P0_1905: planning button has no scroll-relative bounds"',
        'report["stage"] = "P0_1905_numilog_inventory_click"',
        'report["numilog_inventory_click_geometry"] = click_geometry',
        'report.setdefault("numilog_inventory_scroll_history", []).append(',
        'assert attempts <= 8, (',
        'later(160, numilog_inventory_schedule_click)',
        '"P0_1905: planning button outside viewport after scroll"',
    ):
        assert marker in probe
    # No forced focus, synthetic click, skipped gate, or added retry replaces
    # the physical XTEST action or changes any of the existing UX thresholds.
    assert 'report["pointer_actions"].append(device[0].click_widget(button))' in probe


def test_2095_kobo_audit_xtest_requires_stable_real_gtk_hit_target():
    """No programmatic click or retry can mask a lost audit XTEST event."""
    probe = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text(encoding="utf-8")
    assert "w.pick(point_x, point_y, Gtk.PickFlags.DEFAULT)" in probe
    assert "f.get(\"audit_stable_geometry\") != geometry" in probe
    assert "assert attempt <= 8, \"Kobo audit physical hit target did not settle\"" in probe
    assert probe.count("device[0].click_widget(audit)") == 0
    assert probe.count("device[0].window_point(w, point_x, point_y)") == 1
    assert probe.count("device[0].click(sx, sy)") == 1
    assert probe.count("device[0].move(screen_x, screen_y)") == 1
    assert 'motion.connect("enter"' in probe
    assert 'motion.connect("motion"' in probe
    assert 'assert attempts <= 5, "Kobo audit XTEST pointer could not hit GTK button"' in probe
    assert 'f["audit_deadline"] = monotonic() + 6' in probe
    assert 'f["audit_click_signal_seen"] = False' in probe
    assert 'report["kobo_audit_observation"]' in probe

def test_2141_kobo_audit_result_is_scoped_to_offline_account_after_sort():
    """The E2E must inspect the account targeted by the genuine XTEST click."""
    probe = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text(encoding="utf-8")
    tree = ast.parse(probe)
    methods = {node.name: node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef)}
    assert "kobo_offline_audit_row" in methods
    helper = ast.get_source_segment(probe, methods["kobo_offline_audit_row"])
    assert '"Kobo audit hors ligne"' in helper
    assert '"Vérification des fichiers et rapprochements"' in helper
    for method, expected in (
        ("loan_sync_audit_click", "account_group, _ = kobo_offline_audit_row()"),
        ("loan_sync_audit_after_motion", 'f["audit_row_at_click"] = kobo_offline_audit_row()[1]'),
        ("loan_sync_audit_result", "audit_row = kobo_offline_audit_row()[1]"),
    ):
        body = ast.get_source_segment(probe, methods[method])
        assert expected in body
    assert "row for row in walk(w._sync_sources_box)" not in (
        ast.get_source_segment(probe, methods["loan_sync_audit_result"])
    )


def test_771_real_gtk_public_fault_search_is_registered_and_selected():
    runner = _module()
    scenario = runner.SCENARIOS["public-fault-search-1366"]
    assert scenario["runner"] == "gtk-public-fault"
    assert scenario["width"] == 1366
    assert scenario["fixture"] == "search-filters"
    source = (ROOT / "e2e/public_fault_search_probe.py").read_text(encoding="utf-8")
    for marker in (
        "MainWindow(self, core.config, core.library, core.diagnostics)",
        "EbooksSession(", "EbooksGratuitsApp(",
        "httpx.MockTransport", "CatalogSearchBatch(", "CatalogSearchSummary(",
        "device[0].click_widget(ui[0].search_global_button)",
        '"429"', '"503"', '"unavailable"', '"live"',
        "no_implicit_download", "public_status_only",
    ):
        assert marker in source, marker
    import importlib.util
    selector_path = ROOT / "scripts/ci-select-e2e.py"
    spec = importlib.util.spec_from_file_location("ci_select_e2e_p0_771", selector_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    assert module.select(["e2e/public_fault_search_probe.py"]) == (
        "public-fault-search-1366", "kobo-fault-search-1366", "baen-fault-search-1366",
    )


def test_771_kobo_real_catalogue_http_faults_surface_in_native_gtk():
    runner = _module()
    scenario = runner.SCENARIOS["kobo-fault-search-1366"]
    assert scenario["plugin"] == "core"
    assert scenario["runner"] == "gtk-public-fault"
    assert scenario["provider"] == "kobo"
    assert scenario["width"] == 1366
    source = (ROOT / "e2e/public_fault_search_probe.py").read_text(encoding="utf-8")
    for marker in (
        "KoboSession(", "KoboVaultClient(", "KoboCatalogService(",
        "fake.KoboFakeApp(", 'scope="catalog", field="all"',
        "httpx.MockTransport(forward)", "valid_kobo_paths",
        '"kobo-fault-search-1366"', "cache.acsm_dir",
        "device[0].click_widget(ui[0].search_global_button)",
    ):
        assert marker in source, marker
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ci_select_gtk_771_kobo", ROOT / "scripts/ci-select-e2e.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    assert mod.select(["e2e/public_fault_search_probe.py"]) == (
        "public-fault-search-1366", "kobo-fault-search-1366", "baen-fault-search-1366",
    )


def test_2332_baen_native_gtk_offline_partial_and_failures_are_routed():
    runner = _module()
    scenario = runner.SCENARIOS["baen-fault-search-1366"]
    assert scenario["plugin"] == "core"
    assert scenario["fixture"] == "search-filters"
    assert scenario["runner"] == "gtk-public-fault"
    assert scenario["provider"] == "baen"
    assert scenario["width"] == 1366
    source = (ROOT / "e2e/public_fault_search_probe.py").read_text(
        encoding="utf-8"
    )
    for marker in (
        "BaenSession(", "fake.BaenFakeApp(", "www.baen.com",
        "httpx.MockTransport(forward)", "rate-limit", '"down"',
        '"résultats partiels"', "original_epub_digest",
        "device[0].click_widget(ui[0].search_global_button)",
    ):
        assert marker in source, marker
    matrix = (ROOT / "simulation/plugin_coverage.toml").read_text(
        encoding="utf-8"
    )
    section = matrix.split("[package.baen]", 1)[1].split(
        "[package.dev-updater]", 1
    )[0]
    assert 'gtk_scenarios = ["e2e/public_fault_search_probe.py"]' in section
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("selection_2332", ROOT / "scripts/ci-select-e2e.py")
    assert spec and spec.loader
    selector = module_from_spec(spec)
    spec.loader.exec_module(selector)
    assert "baen-fault-search-1366" in selector.select(
        ["e2e/public_fault_search_probe.py"]
    )
