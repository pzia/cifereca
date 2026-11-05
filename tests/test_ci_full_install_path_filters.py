"""Scope the expensive full install/rollback gate without dropping Core changes.

GitHub Actions path filters are ordered: later negative patterns exclude GUI
paths from the earlier Core wildcard. This validates the contract for PR/push.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/core-full-install.yml"


def _paths(event: str) -> tuple[str, ...]:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert event in {"pull_request", "push"}
    trigger = r"^  " + event + r":\n"
    if event == "push":
        trigger += r"    branches: \[main\]\n"
    trigger += r"    paths:\n((?:      - \"[^\"]+\"\n)+)"
    match = re.search(trigger, source, re.MULTILINE)
    assert match, f"missing {event} path filters"
    return tuple(re.findall(r'      - "([^"]+)"', match.group(1)))


def _routed(event: str, *files: str) -> bool:
    patterns = _paths(event)
    for file in files:
        eligible = False
        for entry in patterns:
            exclusion = entry.startswith("!")
            if fnmatchcase(file, entry[1:] if exclusion else entry):
                eligible = not exclusion
        if eligible:
            return True
    return False


def test_full_release_keeps_manual_dispatch_and_symmetrical_scopes():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "  workflow_dispatch:\n" in source
    for event in ("pull_request", "push"):
        patterns = _paths(event)
        positive = patterns.index("src/cifereca/**")
        for exclusion in (
            "!src/cifereca/gui/**",
            "!src/cifereca/builtin_ui/**",
            "!src/cifereca/app.py",
        ):
            assert patterns.index(exclusion) > positive
        assert "scripts/prepare-admitted-wheelhouse.py" in patterns


def test_pure_gtk_and_builtin_ui_changes_are_not_full_release_triggers():
    for event in ("pull_request", "push"):
        for file in (
            "src/cifereca/gui/main_window.py",
            "src/cifereca/gui/dialogs/book_detail.py",
            "src/cifereca/builtin_ui/library.py",
            "src/cifereca/app.py",
        ):
            assert not _routed(event, file), (event, file)


def test_core_updater_and_packaging_changes_keep_the_real_rollback_gate():
    for event in ("pull_request", "push"):
        for file in (
            "src/cifereca/updater.py",
            "src/cifereca/plugins/manager.py",
            "src/cifereca/cli.py",
            "src/cifereca/core/source_materialization.py",
            "src/cifereca/plugin-release.json",
            "src/plugins/provider-paris/cifereca-plugin.json",
            "pyproject.toml",
            "VERSION",
            "scripts/ci-full-release-install-gate.py",
            "scripts/prepare-admitted-wheelhouse.py",
            ".github/workflows/core-full-install.yml",
        ):
            assert _routed(event, file), (event, file)


def test_mixed_gui_and_shared_core_edits_still_trigger_full_install():
    for event in ("pull_request", "push"):
        assert _routed(
            event,
            "src/cifereca/gui/main_window.py",
            "src/cifereca/updater.py",
        )
        assert _routed(event, "src/cifereca/builtin_ui/library.py", "VERSION")
        assert not _routed(event, "docs/README.md", "src/cifereca/gui/book_widgets.py")
