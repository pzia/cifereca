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
    "tests/test_cover_loader.py",
    "tests/test_history_covers.py",
    "tests/test_plugin_cards_*.py",
    "tests/test_regressions_099.py",
)


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Select the smallest safe Core pytest target set for PR CI.")
    parser.add_argument("changed_file", type=Path)
    args = parser.parse_args()
    paths = args.changed_file.read_text(encoding="utf-8").splitlines()
    mode, targets = select(paths)
    print(mode + "\t" + " ".join(targets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
