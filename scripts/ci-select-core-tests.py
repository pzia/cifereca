#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

try:  # support both pytest module loading and direct script execution
    from scripts.ci_test_dependency_guard import has_test_consumers
except ModuleNotFoundError:
    from ci_test_dependency_guard import has_test_consumers

ROOT = Path(__file__).resolve().parents[1]

UI_SOURCE_PREFIXES = (
    "src/cifereca/gui/",
    "src/cifereca/builtin_ui/",
)
UI_SOURCE_FILES = {
    "src/cifereca/app.py",
}
UI_AUX_PREFIXES = (
    "e2e/",
)
UI_AUX_FILES = {
    "environnements/openai-chat-debian/capture_app.py",
}
FULL_CORE_FILES = {
    "pyproject.toml",
    "scripts/ci-select-core-tests.py",
    "scripts/ci_test_dependency_guard.py",
    # #2141: the page's actual GTK composition, action grouping and
    # presentation are covered by legacy test_core_p0_* suites, not only
    # the narrow test_core_ui_* subset. Run ALL Core tests before merging
    # changes to these cross-cutting Synchronisation modules.
    "src/cifereca/gui/sync_controller.py",
    "src/cifereca/gui/sync_action_layout.py",
    "src/cifereca/gui/sync_source_details.py",
    "src/cifereca/gui/sync_view.py",
}
UI_TEST_GLOBS = (
    "tests/test_core_ui_*.py",
    "tests/test_coreui_*.py",  # #1128: headless Search regression suites
    "tests/test_ui_*.py",
    "tests/test_gui_*.py",
    "tests/test_*_ui.py",
    "tests/test_*_ui_contract.py",
    "tests/test_shell_controls_*.py",
    "tests/test_query_controls_*.py",
    "tests/test_builtin_ui_*.py",
    "tests/test_e2e_*.py",
    "tests/test_core09*.py",
    "tests/test_core_09*.py",
    "tests/test_095_consolidation.py",
    "tests/test_core039_contract_surface.py",
    "tests/test_core039_iteration1.py",
    "tests/test_core_412_system_diagnostics_fallback.py",
    "tests/test_core_415_titlebar_action_registry.py",
    "tests/test_core_458_unknown_medium.py",
    "tests/test_core_464_document_links.py",
    "tests/test_core_470_provider_search_capabilities.py",
    "tests/test_core_book_detail_projection.py",
    "tests/test_task_queue_09001.py",
    "tests/test_settings_migration_099_dev2.py",
    "tests/test_adept_manual_binding.py",
    "tests/test_cover_loader.py",
    "tests/test_history_covers.py",
    "tests/test_plugin_cards_*.py",
    "tests/test_regressions_099.py",
)

UI_COMPONENT_SMOKE_TESTS = (
    "tests/test_gui_compat.py",
    "tests/test_regressions_099.py",
    "tests/test_e2e_instrumentation.py",
    "tests/test_e2e_runner_contract.py",
    "tests/test_core_ui_interactive_startup.py",
)

UI_COMPONENT_TEST_GLOBS = {
    "src/cifereca/gui/action_layout.py": (
        "tests/test_core0933_details_actions.py",
        "tests/test_core_ui_375_detail_action_order.py",
        "tests/test_core_ui_376_compact_actions.py",
    ),
    "src/cifereca/gui/action_widgets.py": (
        "tests/test_core_ui_376_compact_actions.py",
        "tests/test_core0933_responsive_widgets.py",
        "tests/test_core0927_detail_layout.py",
        "tests/test_core0933_details_actions.py",
    ),
    "src/cifereca/gui/book_facets.py": (
        "tests/test_core042_library_ui.py",
        "tests/test_core_458_unknown_medium.py",
        "tests/test_core_ui_*facet*.py",
        "tests/test_core_ui_332_filter_value_summary.py",
    ),
    "src/cifereca/gui/book_widgets.py": (
        "tests/test_core0931_cover_preview.py",
        "tests/test_core_ui_362_dense_detail.py",
        "tests/test_core_ui_355_selection_filter.py",
        "tests/test_095_consolidation.py",
        "tests/test_core_ui_detail_badge_layout.py",
        "tests/test_core_ui_376_compact_actions.py",
        "tests/test_core_ui_377_compact_local_path.py",
        "tests/test_core_ui_*facet*.py",
    ),
    "src/cifereca/gui/cover_loader.py": (
        "tests/test_cover_loader.py",
        "tests/test_history_covers.py",
        "tests/test_core0931_cover_preview.py",
        "tests/test_core0929_cover_c03.py",
    ),
    "src/cifereca/gui/cover_preview.py": (
        "tests/test_core0931_cover_preview.py",
    ),
    "src/cifereca/gui/facet_text.py": (
        "tests/test_core_0911.py",
        "tests/test_core_ui_332_filter_value_summary.py",
    ),
    "src/cifereca/gui/navigation_history.py": (
        "tests/test_core039_iteration1.py",
        "tests/test_core0933_refresh_navigation.py",
    ),
    "src/cifereca/gui/plugin_manager_page.py": (
        "tests/test_plugin_cards_097_dev4.py",
        "tests/test_core_415_titlebar_action_registry.py",
        "tests/test_core_ui_startup_actions.py",
    ),
    "src/cifereca/gui/query_controls.py": (
        "tests/test_query_controls_batching.py",
        "tests/test_core_ui_338_filter_tab_order.py",
        "tests/test_core_ui_316_compact_query_filters.py",
        "tests/test_core_ui_332_filter_value_summary.py",
        "tests/test_core_458_unknown_medium.py",
        "tests/test_core_470_provider_search_capabilities.py",
        "tests/test_core_ui_313_progressive_search_feedback.py",
    ),
    "src/cifereca/gui/query_model.py": (
        "tests/test_core0927_list_filter_contract.py",
        "tests/test_ui_iteration_097.py",
        "tests/test_core_0917.py",
        "tests/test_core_ui_095.py",
        "tests/test_core_458_unknown_medium.py",
        "tests/test_core_ui_346_local_dedupe.py",
        "tests/test_core_ui_313_progressive_search_feedback.py",
    ),
    "src/cifereca/gui/search_intent.py": (
        "tests/test_core_0917.py",
        "tests/test_core_ui_online_search_action.py",
        "tests/test_core_ui_313_progressive_search_feedback.py",
    ),
    "src/cifereca/gui/shell_model.py": (
        "tests/test_core_412_system_diagnostics_fallback.py",
        "tests/test_ui_iteration_097.py",
        "tests/test_095_consolidation.py",
        "tests/test_core_ui_095.py",
        "tests/test_core039_contract_surface.py",
        "tests/test_core_415_titlebar_action_registry.py",
        "tests/test_shell_controls_097_dev5.py",
    ),
    "src/cifereca/gui/shortcuts.py": (
        "tests/test_shell_controls_097_dev5.py",
    ),
    "src/cifereca/gui/sync_issues.py": (
        "tests/test_core_0913.py",
        "tests/test_core_ui_sync_actions.py",
    ),
    "src/cifereca/gui/task_runner.py": (
        "tests/test_core0927_task_delivery.py",
        "tests/test_core0927_gtk_lifecycle.py",
        "tests/test_task_queue_09001.py",
    ),
    "src/cifereca/gui/ui_perf.py": (
        "tests/test_task_queue_09001.py",
        "tests/test_core044_performance.py",
    ),
}

E2E_ALL_PREFIXES = ("e2e/",)
E2E_ALL_FILES = {
    "environnements/openai-chat-debian/capture_app.py",
    # Changing the selector itself must validate every E2E route once.
    "scripts/ci-select-core-tests.py",
}
E2E_NAVIGATION_PREFIXES = ("src/cifereca/builtin_ui/",)
E2E_NAVIGATION_FILES = {
    "src/cifereca/gui/main_window.py",
    "src/cifereca/gui/query_controls.py",
    "src/cifereca/gui/shell_model.py",
    "src/cifereca/gui/navigation_history.py",
}
E2E_STARTUP_FILES = {
    "src/cifereca/app.py",
    "src/cifereca/gui/action_widgets.py",
    "src/cifereca/gui/task_runner.py",
    "src/cifereca/gui/plugin_manager_page.py",
    "src/cifereca/gui/ui_perf.py",
}


def _is_ui_source(path: str) -> bool:
    return path in UI_SOURCE_FILES or path.startswith(UI_SOURCE_PREFIXES)


def _is_ui_aux(path: str) -> bool:
    return path in UI_AUX_FILES or path.startswith(UI_AUX_PREFIXES)


def _changed_tests(paths: list[str], root: Path) -> list[str]:
    return sorted(
        path for path in paths
        if path.startswith("tests/test") and path.endswith(".py") and (root / path).is_file()
    )


def _glob_tests(root: Path, patterns: tuple[str, ...]) -> set[str]:
    selected: set[str] = set()
    for pattern in patterns:
        selected.update(str(path.relative_to(root)) for path in root.glob(pattern) if path.is_file())
    return selected


def _ui_tests(root: Path) -> list[str]:
    return sorted(_glob_tests(root, UI_TEST_GLOBS))


def _ui_component_tests(core_sources: list[str], root: Path) -> list[str] | None:
    if not core_sources or any(path not in UI_COMPONENT_TEST_GLOBS for path in core_sources):
        return None
    selected = _glob_tests(root, UI_COMPONENT_SMOKE_TESTS)
    for source in core_sources:
        selected.update(_glob_tests(root, UI_COMPONENT_TEST_GLOBS[source]))
    return sorted(selected)


def select(paths: list[str], root: Path = ROOT) -> tuple[str, list[str]]:
    changed = [path.strip() for path in paths if path.strip()]
    changed_tests = _changed_tests(changed, root)
    # Native installed-recorder assertions run in the dedicated GTK job.
    # The headless PR job still verifies the package/API boundary; selecting
    # only the native module would produce pytest exit 5 (all import-skipped).
    if "tests/test_browser_recorder_installed_gtk_blackbox.py" in changed_tests:
        companion = "tests/test_core_p0_2663_recorder_package_contract.py"
        if not (root / companion).is_file():
            return "full", ["tests"]
        changed_tests = sorted(set(changed_tests) | {companion})

    if any(path in FULL_CORE_FILES for path in changed):
        return "full", ["tests"]

    core_sources = [path for path in changed if path.startswith("src/cifereca/")]
    if any(not _is_ui_source(path) for path in core_sources):
        return "full", ["tests"]

    # Tests are sometimes imported by other suites as fixtures. Running only
    # the changed test misses its consumers; full coverage is the safe default.
    if changed_tests:
        try:
            if has_test_consumers(root, changed_tests):
                return "full", ["tests"]
        except (OSError, SyntaxError, ValueError):
            return "full", ["tests"]

    has_ui_change = bool(core_sources) or any(_is_ui_aux(path) for path in changed)
    if has_ui_change:
        component_targets = _ui_component_tests(core_sources, root)
        if component_targets is not None:
            targets = sorted(set(component_targets) | set(changed_tests))
            return ("ui-component", targets) if targets else ("full", ["tests"])
        targets = sorted(set(_ui_tests(root)) | set(changed_tests))
        return ("ui", targets) if targets else ("full", ["tests"])

    core_relevant_non_test = [
        path for path in changed
        if path == "pyproject.toml" or path.startswith("src/cifereca/")
    ]
    if not core_relevant_non_test and changed_tests:
        return "changed", changed_tests

    return "full", ["tests"]


def select_e2e(paths: list[str]) -> str:
    changed = [path.strip() for path in paths if path.strip()]
    if any(path in E2E_ALL_FILES or path.startswith(E2E_ALL_PREFIXES) for path in changed):
        return "all"
    if any(
        path in E2E_NAVIGATION_FILES or path.startswith(E2E_NAVIGATION_PREFIXES)
        for path in changed
    ):
        return "core-navigation"
    if any(path in E2E_STARTUP_FILES for path in changed):
        return "startup"
    return "none"


def main() -> int:
    parser = argparse.ArgumentParser(description="Select the smallest safe Core/E2E target set for PR CI.")
    parser.add_argument("changed_file", type=Path)
    args = parser.parse_args()
    paths = args.changed_file.read_text(encoding="utf-8").splitlines()
    mode, targets = select(paths)
    print(mode + "\t" + " ".join(targets) + "\t" + select_e2e(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
