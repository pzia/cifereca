"""P0 #2147: PR CI must remain an unconditional, stable merge gate.

GitHub branch protection is configured outside the repository. These offline
contracts prevent a future workflow edit from silently hiding the required
status context on docs-only, metadata-only or governance-only pull requests.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pr-ci.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_pr_ci_has_one_unconditional_pull_request_trigger():
    workflow = _workflow()
    assert workflow.startswith("name: PR CI\n")
    event = workflow.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    assert re.search(r"(?m)^  pull_request:\s*$", event)
    assert "  workflow_dispatch:\n" in event
    assert "    paths:" not in event
    assert "    paths-ignore:" not in event
    assert "    branches:" not in event
    assert "    branches-ignore:" not in event


def test_required_job_identity_and_read_only_permissions_cannot_drift():
    workflow = _workflow()
    assert re.search(r"(?m)^jobs:\n  ci:\n    name: PR CI\s*$", workflow)
    assert re.search(r"(?m)^permissions:\n  contents: read\s*$", workflow)
    assert "  cancel-in-progress: true\n" in workflow
    assert "group: pr-ci-${{ github.event.pull_request.number || github.run_id }}" in workflow
    # A job-level/step-level condition must not silence the one required
    # context; skip expensive steps through the normal scope selector.
    assert not re.search(r"(?m)^  ci:\n(?:    (?!name:)[^\n]*\n)*    if:", workflow)


def test_all_prs_emit_scope_outputs_even_for_docs_only_changes():
    workflow = _workflow()
    assert 'git diff --quiet "$BASE_SHA" "$HEAD_SHA"' in workflow
    assert 'git diff --name-only "$BASE_SHA...$HEAD_SHA"' in workflow
    assert 'if [[ "$GITHUB_EVENT_NAME" != "pull_request" ]]; then' in workflow
    assert 'echo "python=$python"' in workflow
    assert "if: steps.scope.outputs.python == 'true'" in workflow
    assert "if: steps.scope.outputs.core == 'true'" in workflow
    assert "if: steps.scope.outputs.plugins == 'true'" in workflow
    assert "if: steps.scope.outputs.packaging == 'true'" in workflow
    assert "if: steps.scope.outputs.install == 'true'" in workflow
    assert "if: steps.scope.outputs.dataset == 'true'" in workflow


def test_release_workflows_do_not_publish_on_ordinary_push():
    core = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    plugins = (ROOT / ".github/workflows/plugins-only-release.yml").read_text(encoding="utf-8")
    assert '      - ".github/release-trigger"' in core
    assert '      - ".github/plugins-only-release-request.json"' in plugins
    assert "  workflow_dispatch:\n" in core
    assert "  workflow_dispatch:\n" in plugins
    assert "name: Resolve explicitly authorized release request" in plugins
