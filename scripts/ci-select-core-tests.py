#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

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
}
UI_TEST_GLOBS = (
    "tests/test_core_ui_*.py",
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


def _ui_tests(root: Path) -> list[str]:
    selected: set[str] = set()
    for pattern in UI_TEST_GLOBS:
        selected.update(str(path.relative_to(root)) for path in root.glob(pattern) if path.is_file())
    return sorted(selected)


def select(paths: list[str], root: Path = ROOT) -> tuple[str, list[str]]:
    changed = [path.strip() for path in paths if path.strip()]
    changed_tests = _changed_tests(changed, root)

    if any(path in FULL_CORE_FILES for path in changed):
        return "full", ["tests"]

    core_sources = [path for path in changed if path.startswith("src/cifereca/")]
    if any(not _is_ui_source(path) for path in core_sources):
        return "full", ["tests"]

    has_ui_change = bool(core_sources) or any(_is_ui_aux(path) for path in changed)
    if has_ui_change:
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
