"""Check path-scoped expensive GitHub Actions on main (P0 #856).

This inspects only the deliberately simple push.paths YAML fragment:
we test ordered positive/negative globs without adding a YAML dependency.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    "core": ROOT / ".github/workflows/core-tests.yml",
    "plugins": ROOT / ".github/workflows/plugin-tests.yml",
    "install": ROOT / ".github/workflows/core-install.yml",
}


def _push_paths(workflow: Path) -> tuple[str, ...]:
    content = workflow.read_text(encoding="utf-8")
    assert "  workflow_dispatch:\n" in content, workflow
    match = re.search(
        r"(?m)^  push:\n    branches: \[main\]\n    paths:\n"
        r"((?:      - '[^']+'\n)+)",
        content,
    )
    assert match, f"Missing main path filter: {workflow}"
    patterns = tuple(re.findall(r"      - '([^']+)'", match.group(1)))
    assert patterns and any(not value.startswith("!") for value in patterns)
    return patterns


def route(*paths: str) -> set[str]:
    """Ordered GitHub positive/negative pattern semantics on sample paths."""
    selected = set()
    for name, workflow in WORKFLOWS.items():
        patterns = _push_paths(workflow)
        for path in paths:
            eligible = False
            for pattern in patterns:
                negative = pattern.startswith("!")
                if fnmatchcase(path, pattern[1:] if negative else pattern):
                    eligible = not negative
            if eligible:
                selected.add(name)
                break
    return selected


def test_docs_handoffs_release_triggers_run_zero_heavy_suites():
    for path in (
        "docs/design/review.md",
        "AGENTS.md",
        ".agents/roles/focus.md",
        "scripts/README.md",
        ".github/plugins-only-release-request.json",
        ".github/release-trigger",
        ".github/workflows/plugins-only-release.yml",
    ):
        assert route(path) == set(), path
    assert route("docs/notes.md", "AGENTS.md") == set()


def test_plugin_only_runs_plugin_suite_not_core_or_wheel():
    for path in (
        "src/plugins/youth-catalog/youth_page.py",
        "src/plugins/youth-source-milan/tests/test_milan_youth.py",
        "src/plugins/kobo-vault/cifereca-plugin.json",
    ):
        assert route(path) == {"plugins"}, path


def test_gui_only_runs_core_and_install_but_not_plugins():
    for path in (
        "src/cifereca/gui/main_window.py",
        "src/cifereca/builtin_ui/search.py",
        "src/cifereca/app.py",
    ):
        assert route(path) == {"core", "install"}, path


def test_shared_core_api_and_global_build_version_run_all_three():
    for path in (
        "src/cifereca/plugin_api.py",
        "src/cifereca/plugins/manager.py",
        "src/cifereca/storage.py",
        "pyproject.toml",
        "VERSION",
    ):
        assert route(path) == {"core", "plugins", "install"}, path


def test_test_script_and_workflow_consumers_are_explicit():
    expected = {
        "tests/test_updater.py": {"core"},
        "tests/test_ci_main_path_filters.py": {"core"},
        "e2e/youth_page_probe.py": {"core"},
        "environnements/openai-chat-debian/capture_app.py": {"core"},
        "scripts/ci-select-core-tests.py": {"core"},
        "scripts/ci_select_core_push.py": {"core"},
        "scripts/ci_core_release_preflight.py": {"core"},
        "scripts/ci-install-gtk-e2e-runtime.sh": {"core"},
        "scripts/package_partial_plugins.py": {"core"},
        "scripts/plugin_packaging.py": {"core"},
        "scripts/test_plugin_packaging.py": {"core"},
        "scripts/package-dev-snapshot.py": {"core"},
        "scripts/test_partial_plugin_release.py": {"core"},
        "scripts/ci-run-plugin-tests.sh": {"plugins"},
        "scripts/ci-smoke-installed-wheel.sh": {"install"},
        "README.md": {"install"},  # wheel description metadata
    }
    for path, suites in expected.items():
        assert route(path) == suites, path


def test_each_workflow_self_trigger_and_manual_dispatch_stays_available():
    for name, workflow in WORKFLOWS.items():
        assert route(str(workflow.relative_to(ROOT))) == {name}
        source = workflow.read_text(encoding="utf-8")
        assert "  workflow_dispatch:\n" in source
        # Required PR checks must not become indefinitely pending due to paths.
        assert "  pull_request:" not in source


def test_mixed_changes_run_union_of_relevant_suites():
    assert route("docs/changelog.md", "src/plugins/youth-source-milan/milan_youth.py") == {"plugins"}
    assert route("src/cifereca/gui/shell_model.py", "src/plugins/youth-catalog/youth_catalog.py") == {
        "core", "plugins", "install",
    }
    assert route("scripts/ci-run-plugin-tests.sh", "scripts/ci-smoke-installed-wheel.sh") == {
        "plugins", "install",
    }


def test_git_glob_exclusions_follow_positive_include():
    patterns = _push_paths(WORKFLOWS["plugins"])
    base = patterns.index("src/cifereca/**")
    for negative in (
        "!src/cifereca/gui/**",
        "!src/cifereca/builtin_ui/**",
        "!src/cifereca/app.py",
    ):
        assert patterns.index(negative) > base
