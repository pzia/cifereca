"""P0 #2147: required PR CI must always produce one stable status context.

The branch protection rule is managed by a repository administrator; this
contract only makes the job eligible for all PRs, while its expensive scopes
remain selected by the changed paths inside the job.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pr-ci.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _matched_path(patterns: list[str], path: str) -> bool:
    return any(re.search(pattern, path) is not None for pattern in patterns)


def test_documentation_only_still_emits_ci_without_heavy_validation():
    source = _workflow()
    patterns = re.findall(r"if matches '([^']+)'", source)
    assert patterns
    for path in (
        "docs/example.md", "AGENTS.md", ".agents/roles/cicd.md",
        ".github/release-trigger",
    ):
        assert not _matched_path(patterns, path), path
    assert "if [[ \"$core\" == true || \"$plugins\" == true" in source
    assert "python=false" in source


def test_readme_metadata_changes_validate_installed_wheel():
    source = _workflow()
    conditions = re.findall(
        r"(?m)^          if matches '([^']+)'; then\n            install=true", source
    )
    assert len(conditions) == 1
    assert re.search(conditions[0], "README.md")
    assert not re.search(conditions[0], "docs/changelog.md")
    assert re.search(conditions[0], "VERSION")
    assert "if: steps.scope.outputs.install == 'true'" in source
    assert "bash scripts/ci-smoke-installed-wheel.sh" in source


def test_httpx_ast_gate_is_scoped_to_plugin_and_guard_changes():
    source = _workflow()
    assert "if: steps.scope.outputs.httpx_boundary == 'true'" in source
    assert 'echo "httpx_boundary=true"' in source  # manual dispatch
    assert 'echo "httpx_boundary=$httpx_boundary"' in source
    scope = re.search(
        r"(?m)^          if matches '([^']+)'; then\n            httpx_boundary=true$",
        source,
    )
    assert scope, "missing dedicated HTTPX selection"
    pattern = scope.group(1)
    for path in (
        "src/plugins/provider-numilog/cifereca_numilog.py",
        "scripts/ci_plugin_httpx_boundary.py",
        "tests/test_ci_plugin_httpx_boundary.py",
        ".github/workflows/pr-ci.yml",
    ):
        assert re.search(pattern, path), path
    for path in ("docs/notes.md", "README.md", "tests/test_storage.py",
                 "src/cifereca/gui/main_window.py", ".agents/roles/cicd.md"):
        assert not re.search(pattern, path), path
