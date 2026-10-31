from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "scripts/ci-select-core-tests.py"


def _module():
    spec = importlib.util.spec_from_file_location("ci_select_core_tests", SELECTOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_test_only_change_runs_only_changed_test_file():
    selector = _module()
    mode, targets = selector.select(["tests/test_storage.py"], ROOT)
    assert mode == "changed"
    assert targets == ["tests/test_storage.py"]


def test_gui_component_change_uses_component_suite():
    selector = _module()
    mode, targets = selector.select(["src/cifereca/gui/query_controls.py"], ROOT)
    assert mode == "ui-component"
    assert "tests/test_query_controls_batching.py" in targets
    assert "tests/test_core_ui_interactive_startup.py" in targets
    assert "tests/test_core_ui_338_filter_tab_order.py" in targets
    assert "tests/test_storage.py" not in targets
    assert len(targets) < 20


def test_gui_change_always_includes_changed_regression_test():
    selector = _module()
    mode, targets = selector.select(
        ["src/cifereca/gui/main_window.py", "tests/test_storage.py"],
        ROOT,
    )
    assert mode == "ui"
    assert "tests/test_storage.py" in targets


def test_non_gui_core_change_keeps_full_suite():
    selector = _module()
    mode, targets = selector.select(["src/cifereca/services.py"], ROOT)
    assert (mode, targets) == ("full", ["tests"])


def test_pyproject_or_selector_change_keeps_full_suite():
    selector = _module()
    assert selector.select(["pyproject.toml"], ROOT) == ("full", ["tests"])
    assert selector.select(["scripts/ci-select-core-tests.py"], ROOT) == ("full", ["tests"])
    assert selector.select(["scripts/ci_test_dependency_guard.py"], ROOT) == ("full", ["tests"])


def test_e2e_change_uses_ui_suite():
    selector = _module()
    mode, targets = selector.select(["e2e/run.py"], ROOT)
    assert mode == "ui"
    assert "tests/test_e2e_runner_contract.py" in targets


def test_e2e_selector_uses_one_navigation_scenario_for_main_ui_changes():
    selector = _module()
    assert selector.select_e2e(["src/cifereca/gui/main_window.py"]) == "core-navigation"
    assert selector.select_e2e(["src/cifereca/gui/query_controls.py"]) == "core-navigation"


def test_e2e_selector_uses_startup_only_for_startup_helpers():
    selector = _module()
    assert selector.select_e2e(["src/cifereca/app.py"]) == "startup"
    assert selector.select_e2e(["src/cifereca/gui/task_runner.py"]) == "startup"


def test_e2e_selector_skips_noncritical_gui_helpers():
    selector = _module()
    assert selector.select_e2e(["src/cifereca/gui/cover_loader.py"]) == "none"
    assert selector.select_e2e(["src/cifereca/gui/book_widgets.py"]) == "none"


def test_e2e_selector_runs_all_when_runner_or_selector_changes():
    selector = _module()
    assert selector.select_e2e(["e2e/run.py"]) == "all"
    assert selector.select_e2e(["scripts/ci-select-core-tests.py"]) == "all"


def test_ci_test_dependencies_exclude_unused_pyinstaller():
    pr_ci = (ROOT / ".github/workflows/pr-ci.yml").read_text()
    core_ci = (ROOT / ".github/workflows/core-tests.yml").read_text()
    assert '-e ".[dev]"' not in pr_ci
    assert '-e ".[dev]"' not in core_ci
    assert '"pytest>=8,<9"' in pr_ci
    assert '"pytest>=8,<9"' in core_ci


def test_ci_installs_pymupdf_only_for_epub_optimizer_or_full_plugin_scope():
    pr_ci = (ROOT / ".github/workflows/pr-ci.yml").read_text()
    assert '" $PLUGIN_TARGETS " == *" src/plugins/epub_optimizer "*' in pr_ci
    assert 'if [[ -z "$PLUGIN_TARGETS"' in pr_ci


def test_gui_change_keeps_legacy_and_cross_cutting_ui_regressions():
    selector = _module()
    mode, targets = selector.select(["src/cifereca/gui/main_window.py"], ROOT)
    assert mode == "ui"
    assert "tests/test_core_0911.py" in targets
    assert "tests/test_core0923_gtk_focus.py" in targets
    assert "tests/test_095_consolidation.py" in targets
    assert "tests/test_core_415_titlebar_action_registry.py" in targets
    assert "tests/test_core_book_detail_projection.py" in targets


def test_book_facets_and_widgets_use_small_regression_bundle():
    selector = _module()
    mode, targets = selector.select(
        [
            "src/cifereca/gui/book_facets.py",
            "src/cifereca/gui/book_widgets.py",
        ],
        ROOT,
    )
    assert mode == "ui-component"
    assert "tests/test_core_ui_531_bounded_facets.py" in targets
    assert "tests/test_core_ui_362_dense_detail.py" in targets
    assert "tests/test_core_ui_355_selection_filter.py" in targets
    assert "tests/test_core_ui_interactive_startup.py" in targets
    assert "tests/test_storage.py" not in targets
    assert len(targets) < 25


def test_main_window_keeps_conservative_ui_suite():
    selector = _module()
    mode, targets = selector.select(["src/cifereca/gui/main_window.py"], ROOT)
    assert mode == "ui"
    assert "tests/test_core_0911.py" in targets
    assert "tests/test_core0923_gtk_focus.py" in targets
    assert len(targets) > 30


def test_component_change_always_keeps_explicit_changed_regression():
    selector = _module()
    mode, targets = selector.select(
        ["src/cifereca/gui/cover_loader.py", "tests/test_storage.py"],
        ROOT,
    )
    assert mode == "ui-component"
    assert "tests/test_storage.py" in targets

def test_2141_sync_page_changes_cannot_skip_cross_cutting_p0_regressions():
    """The 4 post-merge failures escaped the narrow CoreUI selector."""
    selector = _module()
    for path in (
        "src/cifereca/gui/sync_controller.py",
        "src/cifereca/gui/sync_action_layout.py",
        "src/cifereca/gui/sync_source_details.py",
        "src/cifereca/gui/sync_view.py",
    ):
        assert selector.select([path], ROOT) == ("full", ["tests"])
        assert selector.select([path, "tests/test_core_ui_1347_sync_action_scope.py"], ROOT) == (
            "full", ["tests"]
        )
    # Keep unrelated GUI component PRs fast: never globally widen the selector.
    assert selector.select(["src/cifereca/gui/query_controls.py"], ROOT)[0] == "ui-component"


def test_installed_recorder_shared_fixture_requires_full_core_and_real_gtk_gate():
    selector = _module()
    native = "tests/test_browser_recorder_installed_gtk_blackbox.py"
    # Two other test modules import helpers from this native GTK module;
    # selecting only its companion test would hide regressions in consumers.
    assert selector.select(
        [native, "src/plugins/browser-recorder/browser_recorder_page.py"], ROOT
    ) == ("full", ["tests"])
    workflow = (ROOT / ".github/workflows/browser-recorder-chromium-e2e.yml").read_text()
    assert native in workflow  # dedicated native gate remains mandatory


def test_installed_recorder_missing_headless_contract_fails_to_full_selection(tmp_path):
    selector = _module()
    native = "tests/test_browser_recorder_installed_gtk_blackbox.py"
    (tmp_path / "tests").mkdir()
    (tmp_path / native).write_text("# synthetic native test")
    assert selector.select([native], tmp_path) == ("full", ["tests"])


def test_imported_test_fixture_forces_full_core_selection(tmp_path):
    selector = _module()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fixture.py").write_text("VALUE = 1\n")
    (tests / "test_consumer.py").write_text("from tests.test_fixture import VALUE\n")
    assert selector.select(["tests/test_fixture.py"], tmp_path) == ("full", ["tests"])


def test_test_filename_used_only_as_data_remains_targeted(tmp_path):
    selector = _module()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fixture.py").write_text("VALUE = 1\n")
    (tests / "test_consumer.py").write_text('PATH = "tests/test_fixture.py"\n')
    assert selector.select(["tests/test_fixture.py"], tmp_path) == (
        "changed", ["tests/test_fixture.py"]
    )


def test_dynamic_and_pytest_plugin_consumers_prevent_narrow_selection(tmp_path):
    selector = _module()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fixture.py").write_text("VALUE = 1\n")
    for consumer in (
        'importlib.import_module("tests." + name)\n',
        'pytest_plugins = ["tests.test_fixture"]\n',
    ):
        (tests / "test_consumer.py").write_text(consumer)
        assert selector.select(["tests/test_fixture.py"], tmp_path) == ("full", ["tests"])
    (tests / "test_consumer.py").write_text('pytest_plugins = ["pytest_cov"]\n')
    assert selector.select(["tests/test_fixture.py"], tmp_path) == (
        "changed", ["tests/test_fixture.py"]
    )


def test_pr_ci_forces_full_for_removed_or_renamed_test_modules():
    workflow = (ROOT / ".github/workflows/pr-ci.yml").read_text(encoding="utf-8")
    assert 'git diff --name-status "$BASE_SHA...$HEAD_SHA" -- tests/' in workflow
    assert "grep -Eq '^(D|R[0-9]*|C[0-9]*|T|U)[[:space:]]'" in workflow
    assert "core_mode=full" in workflow
    assert "core_targets=tests" in workflow
