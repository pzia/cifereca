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


def test_gui_change_uses_focused_ui_suite():
    selector = _module()
    mode, targets = selector.select(["src/cifereca/gui/query_controls.py"], ROOT)
    assert mode == "ui"
    assert "tests/test_query_controls_batching.py" in targets
    assert "tests/test_core_ui_interactive_startup.py" in targets
    assert "tests/test_storage.py" not in targets


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
