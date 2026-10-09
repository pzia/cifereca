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
        "search-filter-p0", "startup", "core-navigation",
    ]


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


def test_youth_booktrust_replay_uses_real_installed_provider_and_offline_http():
    runner = _module()
    names = [name for name, _ in runner.select(None, "catalog.youth")]
    assert names == [
        "youth-1024", "youth-1366", "youth-booktrust-1024", "youth-booktrust-1366",
        "youth-milan-1024", "youth-milan-1366",
        "youth-editions-1024", "youth-editions-1366",
    ]
    for name in names[2:4]:
        scenario = runner.SCENARIOS[name]
        assert scenario["runner"] == "gtk-youth"
        assert scenario["fixture"] == "search-filters"
        assert scenario["booktrust"] is True
        assert scenario["width"] in (1024, 1366)
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert '"youth.source.booktrust"' in source
    assert "httpx.MockTransport(respond)" in source
    assert 'device.click_widget(collect_button())' in source
    assert "booktrust_requests_after_outage" in source
    assert "03-local-empty-distinct-from-outage" in source


def test_youth_milan_replay_uses_installed_source_and_safe_notice_action():
    runner = _module()
    for name in ("youth-milan-1024", "youth-milan-1366"):
        scenario = runner.SCENARIOS[name]
        assert scenario["plugin"] == "catalog.youth"
        assert scenario["runner"] == "gtk-youth"
        assert scenario["milan"] is True
        assert scenario["width"] in (1024, 1366)
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert '"youth.source.milan"' in source
    assert 'httpx.MockTransport(milan_respond)' in source
    assert 'b.get_uri()' in source
    assert "milan_page_two" in source
    assert "milan_outage" in source
    assert "05-milan-empty-without-request" in source


def test_youth_conflicting_editions_run_through_existing_real_gtk_runner():
    runner = _module()
    for name in ("youth-editions-1024", "youth-editions-1366"):
        scenario = runner.SCENARIOS[name]
        assert scenario["runner"] == "gtk-youth"
        assert scenario["fixture"] == "search-filters"
        assert scenario["editions"] is True
        assert scenario["width"] in (1024, 1366)
        assert not scenario.get("booktrust") and not scenario.get("milan")
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
    assert '("Référence source", "booktrust:unknown") in shown' in source


def test_youth_active_sources_retry_only_after_explicit_gtk_click():
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert "def booktrust_retry_after_outage():" in source
    assert "def booktrust_recovered():" in source
    assert '"04-source-recovered-after-503"' in source
    assert 'assert requests[0] == requests[1] == requests[2]' in source
    assert "def milan_retry_after_outage():" in source
    assert "def milan_recovered():" in source
    assert '"06-milan-recovered-first-page"' in source
    assert "len(requests) == 22" in source
    assert 'assert len(page.view.rows) == 4, "Authoritative recovery' in source
