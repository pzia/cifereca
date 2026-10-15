from __future__ import annotations

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
        "search-filter-p0", "book-detail-1366", "book-detail-1600",
        "mainwindow-baseline-1366", "mainwindow-baseline-1600",
        "llm-settings-1366", "llm-settings-1600",
        "maintenance-tools-1366", "maintenance-tools-1600",
        "startup", "core-navigation",
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
    for width in (1366, 1600):
        scenario = runner.SCENARIOS[f"maintenance-tools-{width}"]
        assert scenario["plugin"] == "core"
        assert scenario["fixture"] == "startup"
        assert scenario["runner"] == "gtk-maintenance"
        assert scenario["width"] == width
    for marker in (
        'w.navigate("tools")',
        'device[0].click_widget(button)',
        "busy_sync_refuses_preview_before_scan_or_delete",
        "real_multiline_confirmation_after_async_scan",
        "physical_cancel_never_runs_destructive_tool",
        "stale_plan_rejected_under_lock_without_delete",
        "only_explicit_confirmed_safe_simulation_executes",
        'report["status"] = "PASS"',
    ):
        assert marker in source


def test_942_shared_book_detail_uses_real_gtk_and_offline_copy_search():
    runner = _module()
    for width in (1366, 1600):
        scenario = runner.SCENARIOS[f"book-detail-{width}"]
        assert scenario["plugin"] == "core"
        assert scenario["fixture"] == "search-filters"
        assert scenario["runner"] == "gtk-detail"
        assert scenario["width"] == width
    assert [n for n, _ in runner.select(None, "core")][:3] == [
        "search-filter-p0", "book-detail-1366", "book-detail-1600",
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


def test_youth_922_real_gtk_filter_help_is_explicit_and_1366_only():
    runner = _module()
    scenario = runner.SCENARIOS["youth-filter-help-1366"]
    assert scenario["plugin"] == "catalog.youth"
    assert scenario["runner"] == "gtk-youth"
    assert scenario["filters_ux"] is True
    assert scenario["width"] == 1366
    assert "youth-filter-help-1024" not in runner.SCENARIOS
    script = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    runner_script = RUNNER.read_text(encoding="utf-8")
    for token in (
        '"--filters-ux"', "def filter_help_snapshot():",
        "def filter_help_start_ui():", "def filter_help_after_input():",
        'device.type_text("Forest")',
        '"filter_help_missing_labels"', '"filter_help_missing_tooltips"',
        '"filter_help_lost_labels_after_input"', "assert not requests",
        '"00-jeunesse-controles-1366"', '"01-jeunesse-controles-saisie-1366"',
        '"02-jeunesse-navigation-clavier-1366"',
        "def filter_help_tab_step(phase, index):",
        "def filter_help_tab_record(phase, index):",
        "def filter_help_keyboard_finish():",
        'device.key("Shift_L", True)', 'keys("Tab")',
        'focused.is_ancestor(control)',
        '"filter_help_keyboard"', '"missing_controls"',
        '"anonymous_forward_filter_stops"',
        '"anonymous_backward_filter_stops"',
        'if name == "other:GtkFlowBoxChild"',
        'assert not requests, "Tab/Shift+Tab ne doivent jamais déclencher de collecte"',
    ):
        assert token in script or token in runner_script


def test_youth_milan_replay_uses_installed_source_and_safe_notice_action():
    runner = _module()
    for name in ("youth-milan-1366",):
        scenario = runner.SCENARIOS[name]
        assert scenario["plugin"] == "catalog.youth"
        assert scenario["runner"] == "gtk-youth"
        assert scenario["milan"] is True
        assert scenario["width"] == 1366
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert '"youth.source.milan"' in source
    assert 'httpx.MockTransport(milan_respond)' in source
    assert 'b.get_uri()' in source
    assert "milan_page_two" in source
    assert "milan_outage" in source
    assert "05-milan-empty-without-request" in source


def test_youth_conflicting_editions_run_through_existing_real_gtk_runner():
    runner = _module()
    for name in ("youth-editions-1366",):
        scenario = runner.SCENARIOS[name]
        assert scenario["runner"] == "gtk-youth"
        assert scenario["fixture"] == "search-filters"
        assert scenario["editions"] is True
        assert scenario["width"] == 1366
        assert not scenario.get("milan")
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert 'same_isbn = "9780306406157"' in source
    assert 'other_isbn = "9780140328721"' in source
    assert 'assert set(by_isbn) == {same_isbn, other_isbn}' in source
    assert '"01-conflicting-age-sources-preserved"' in source
    assert '"02-second-edition-separated"' in source
    assert '"07-other-profile-stays-empty"' in source

    # #891: assertions target actual mapped GTK list cell labels plus the
    # Adw.ActionRows of both selected editions, not only catalog records.
    assert 'assert cell.meta_label.get_mapped()' in source
    assert 'cell.meta_label.get_text().startswith(f"ISBN {isbn}")' in source
    assert '("ISBN", "9780306406157") in shown' in source
    assert '("ISBN", "9780140328721") in shown' in source
    assert '("Référence source", "fixture-youth:unknown") in shown' in source


def test_youth_active_sources_retry_only_after_explicit_gtk_click():
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert "def milan_retry_after_outage():" in source
    assert "def milan_recovered():" in source
    assert '"06-milan-recovered-first-page"' in source
    assert "len(requests) == 22" in source
    assert 'assert len(page.view.rows) == 4, "Authoritative recovery' in source


def test_youth_notice_real_gtk_click_launches_isolated_https_sink_only():
    # #758: ensure the real widget is clicked, and the resulting Gio-default
    # handler invocation is asserted (a mere Gtk.LinkButton.uri isn't proof).
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert "Gio.AppInfo.create_from_commandline(" in source
    assert 'set_as_default_for_type("x-scheme-handler/https")' in source
    assert 'Gio.AppInfo.get_default_for_uri_scheme("https")' in source
    assert 'assert not notice_sink_path.exists()' in source
    assert 'device.click_widget(notice[0])' in source
    assert 'assert invocations == [[result["milan_notice_uri"]]]' in source
    assert '"02b-milan-notice-launched-to-offline-browser"' in source
    assert 'assert len(requests) == 9, "Clicking notice must not re-collect' in source


def test_youth_plugin_manager_real_gtk_inspection_and_keyboard_search():
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert "def open_installed_plugin_manager():" in source
    assert 'window.navigate("plugins")' in source
    assert 'manager.get_mapped()' in source
    assert 'device.click_widget(manager.plugin_search)' in source
    assert 'device.type_text("youth")' in source
    assert '"09-real-plugin-manager-installed"' in source
    assert '"10-real-plugin-manager-youth-search"' in source
    assert '"11-youth-restored-after-plugin-manager"' in source
    assert 'plugin_states() == result["plugin_states_before_manager"]' in source


def test_youth_enter_observation_uses_real_xtest_and_never_marks_issue_fixed():
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert 'page.view.query.connect("activate", activated)' in source
    assert 'keys("Return")' in source
    assert 'keys("KP_Enter")' in source
    assert 'audit["gtk_activate_signals"] == 1' in source
    assert 'audit["gtk_activate_signals"] == 2' in source
    assert 'assert requests == [], "No selected source:' in source
    assert 'audit["criterion_910_satisfied"] = False' in source
    assert 'assert audit["return_visible_feedback"]' in source
    assert 'assert audit["keypad_visible_feedback"]' in source
    assert '"05b-youth-return-gtk-observation"' in source
    assert '"05c-youth-keypad-enter-gtk-observation"' in source




def test_youth_milan_real_gtk_actions_track_collection_cursor_outage_and_selection():
    """#923 real GTK must follow offline Milan's actual state transitions."""
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert "def audit_milan_action_states(stage, expected):" in source
    assert 'result.setdefault("milan_action_states_923", []).append({' in source
    assert "button.get_sensitive()" in source
    assert "button.get_tooltip_text()" in source
    for stage in (
        "empty-all-sources", "milan-selected-no-results", "milan-first-page",
        "milan-book-selected", "milan-terminal-page",
        "milan-outage-cache-retained", "milan-filtered-empty",
        "milan-recovered",
    ):
        assert f'audit_milan_action_states("{stage}",' in source
    assert 'assert requests == [], "Opening Milan page triggered remote HTTP"' in source
    assert 'assert not requests, "Selecting Milan triggered remote HTTP"' in source
    assert 'assert len(requests) == 13, f"Outage requires exactly one new explicit call:' in source



def test_youth_no_source_action_gtk_is_disabled_and_explained_without_click():
    """UX #923: preserve a screenshot and prove the widget's disabled state."""
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert "def inspect_collect_without_source():" in source
    assert 'assert not button.get_sensitive()' in source
    assert 'reason = button.get_tooltip_text() or ""' in source
    assert 'page.view.actions_hint.get_mapped()' in source
    assert 'assert requests == [], "Disabled no-source action must not start HTTP"' in source
    assert '"no_source_action_state"' in source
    assert '"07-no-source-feedback"' in source



def test_906_real_gtk_library_search_is_scoped_and_keyboard_driven():
    runner = _module()
    for width in (1366,):
        scenario = runner.SCENARIOS[f"youth-library-search-{width}"]
        assert scenario["plugin"] == "catalog.youth"
        assert scenario["runner"] == "gtk-youth"
        assert scenario["library_search"] is True
        assert scenario["width"] == width
        assert scenario["fixture"] == "search-filters"
    runner_source = RUNNER.read_text(encoding="utf-8")
    assert '"--library-search"' in runner_source
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    for required in (
        'def library_search_start_ui():',
        'def library_search_verify_local():',
        'def library_search_verify_return():',
        'def library_search_verify_enter():',
        'def library_search_verify_space():',
        'device.click_widget(action)',
        'keys("Return")',
        'keys("space")',
        'assert window._search_mode == "local" and not online_attempts',
    ):
        assert required in source


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
