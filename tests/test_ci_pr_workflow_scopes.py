"""Exercise the exact PR CI Bash policy for specialized workflow edits (#3926).

Synthetic changed-file lists only: no GitHub token, GTK or network.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pr-ci.yml"
BEGIN = "          if matches '^\\.github/workflows/'; then\n"
END = "\n          python=false\n"


def _shell_policy() -> str:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert source.count(BEGIN) == 1, "required workflow scope gate changed"
    body = source.split(BEGIN, 1)[1].split(END, 1)[0]
    return (
        "set -euo pipefail\n"
        'changed_file="$1"\n'
        'matches() { grep -Eq "$1" "$changed_file"; }\n'
        "core=false\n"
        "core_mode=none\n"
        'core_targets=""\n'
        "plugins=false\n"
        "plugin_scope_all=false\n"
        "packaging=false\n"
        "install=false\n"
        "dataset=false\n"
        + BEGIN
        + body
        + "\n"
        + 'printf "core=%s\nmode=%s\ntargets=%s\nplugins=%s\nplugin_scope_all=%s\npackaging=%s\ninstall=%s\ndataset=%s\n" '
        + '"$core" "$core_mode" "$core_targets" "$plugins" "$plugin_scope_all" '
        + '"$packaging" "$install" "$dataset"\n'
    )


def _scope(tmp_path: Path, *paths: str, cwd: Path = ROOT) -> dict[str, str]:
    changed = tmp_path / "changed-files.txt"
    changed.write_text("\n".join(paths) + "\n", encoding="utf-8")
    run = subprocess.run(
        ["bash", "-c", _shell_policy(), "--", str(changed)],
        cwd=cwd, check=True, capture_output=True, text=True, timeout=10,
    )
    return dict(line.split("=", 1) for line in run.stdout.splitlines())


@pytest.mark.parametrize(
    ("path", "targets", "expected"),
    [
        (
            ".github/workflows/core-tests.yml",
            ("tests/test_ci_core_main_selection.py", "tests/test_ci_main_path_filters.py"),
            {"core": "true", "mode": "ci-contract", "plugins": "false", "install": "false", "dataset": "false"},
        ),
        (
            ".github/workflows/plugin-tests.yml",
            ("tests/test_ci_main_path_filters.py", "tests/test_ci_plugin_selection.py"),
            {"core": "true", "mode": "ci-contract", "plugins": "true", "plugin_scope_all": "true", "packaging": "false"},
        ),
        (
            ".github/workflows/e2e-smoke.yml",
            ("tests/test_ci_2027_e2e_replay_completeness.py", "tests/test_ci_select_e2e.py"),
            {"core": "true", "mode": "ci-contract", "plugins": "false", "packaging": "false", "install": "false", "dataset": "false"},
        ),
        (
            ".github/workflows/core-install.yml",
            ("tests/test_ci_main_path_filters.py",),
            {"core": "true", "mode": "ci-contract", "plugins": "false", "install": "true"},
        ),
    ],
)
def test_only_relevant_workflow_contract_tests_run(tmp_path, path, targets, expected):
    selected = _scope(tmp_path, path)
    for key, value in expected.items():
        assert selected[key] == value, (path, key, selected)
    for name in targets:
        assert name in selected["targets"].split(), (path, name)
        assert (ROOT / name).is_file(), name
    assert selected["targets"] != "tests"


def test_dataset_workflow_runs_simulation_and_its_workflow_contract(tmp_path):
    selected = _scope(tmp_path, ".github/workflows/dataset-harness.yml")
    assert selected["dataset"] == "true"
    assert selected["core"] == "true"
    assert selected["mode"] == "ci-contract"
    assert selected["targets"] == "tests/test_ci_pr_workflow_scopes.py"
    assert selected["plugins"] == "false"
    assert selected["packaging"] == "false"
    assert selected["install"] == "false"


@pytest.mark.parametrize("name", [
    ".github/workflows/pr-ci.yml",
    ".github/workflows/plugins-only-release.yml",
    ".github/workflows/release.yml",
    ".github/workflows/core-full-install.yml",
    ".github/workflows/unknown-future.yml",
])
def test_unrecognized_or_security_critical_workflows_keep_exhaustive_ci(tmp_path, name):
    selected = _scope(tmp_path, name)
    assert selected["mode"] == "full"
    assert selected["targets"] == "tests"
    for flag in ("core", "plugins", "plugin_scope_all", "packaging", "install", "dataset"):
        assert selected[flag] == "true", (name, flag)


@pytest.mark.parametrize("extra", [
    "docs/changelog.md",
    "src/cifereca/core/storage.py",
    "tests/test_storage.py",
    ".github/workflows/plugin-tests.yml",
])
def test_mixed_changes_keep_exhaustive_validation(tmp_path, extra):
    selected = _scope(tmp_path, ".github/workflows/core-tests.yml", extra)
    assert selected["mode"] == "full"
    assert selected["targets"] == "tests"
    for flag in ("core", "plugins", "packaging", "install", "dataset"):
        assert selected[flag] == "true", extra


def test_required_ci_context_and_manual_dispatch_are_unchanged():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "  pull_request:\n" in source
    assert "  workflow_dispatch:\n" in source
    assert "    name: PR CI\n" in source
    assert "  cancel-in-progress: true\n" in source
    manual = source.split('if [[ "$GITHUB_EVENT_NAME" != "pull_request" ]]; then', 1)[1]
    manual = manual.split("\n          fi\n", 1)[0]
    assert 'echo "core_mode=full"' in manual
    assert 'echo "plugins=true"' in manual
    assert 'echo "dataset=true"' in manual


def test_workflow_only_e2e_edit_cannot_disable_its_real_pr_gate():
    source = (ROOT / ".github/workflows/e2e-smoke.yml").read_text(encoding="utf-8")
    event = source.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    assert "  pull_request:\n    paths:\n" in event
    assert "      - '.github/workflows/e2e-smoke.yml'\n" in event
    assert "tests/test_ci_pr_workflow_scopes.py" in _scope(Path(__file__).parent, ".github/workflows/e2e-smoke.yml")["targets"].split()
    assert "    name: E2E GTK · core\n" in source
    assert "    timeout-minutes: 15\n" in source
    assert "continue-on-error: true" not in source


@pytest.mark.parametrize("deleted", [
    ".github/workflows/core-tests.yml",
    ".github/workflows/plugin-tests.yml",
    ".github/workflows/e2e-smoke.yml",
    ".github/workflows/core-install.yml",
    ".github/workflows/dataset-harness.yml",
])
def test_deleted_specialized_workflow_is_rejected(tmp_path, deleted):
    # The synthetic checkout has no specialized workflow file. The same Bash
    # policy must fail rather than report successful scoped validation.
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _scope(tmp_path, deleted, cwd=tmp_path)
    assert "Specialized CI workflow was deleted" in exc.value.stderr


def test_dataset_only_edits_cannot_disable_manual_smoke_contract():
    source = (ROOT / ".github/workflows/dataset-harness.yml").read_text(encoding="utf-8")
    event = source.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    assert "  workflow_dispatch:\n" in event
    assert "  pull_request:" not in event
    for command in (
        "python -m unittest discover -s simulation/tests -v",
        "python simulation/datasetctl.py up ux-small",
        "python simulation/datasetctl.py status ux-small",
        "python simulation/datasetctl.py config ux-small",
        "python simulation/datasetctl.py down ux-small",
    ):
        assert command in source, command
    assert "if: always()" in source
