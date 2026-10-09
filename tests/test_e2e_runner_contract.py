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


def test_youth_p1_real_gtk_scenarios_have_isolated_fixture_and_two_resolutions():
    runner = _module()
    names = [name for name, _ in runner.select(None, "org.cifereca.youth-catalog")]
    assert names == ["youth-page-1024", "youth-page-1366"]
    assert {runner.SCENARIOS[name]["screen"] for name in names} == {
        "1024x900x24", "1366x900x24",
    }
    for name in names:
        scenario = runner.SCENARIOS[name]
        assert scenario["runner"] == "gtk-probe"
        assert scenario["probe"] == "youth_page_probe.py"
        assert scenario["fixture"] == "youth-page"
    import json
    config = json.loads((ROOT / "e2e/fixtures/youth-page/config.json").read_text())
    assert [row["id"] for row in config["identities"]] == ["camille", "alex"]
    source = (ROOT / "e2e/youth_page_probe.py").read_text(encoding="utf-8")
    assert 'MainWindow(self, core.config, core.library, core.diagnostics)' in source
    assert 'PackageInstaller(PackageStore()).install(archive)' in source
    assert 'device.click_widget(view.query)' in source
    assert 'device.type_text("Atlas")' in source
    assert 'catalog.search_cached("camille", age=18)' in source
