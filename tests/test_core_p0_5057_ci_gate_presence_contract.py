"""P0 #5057: pin the required provider-neutral Core CI step contract."""
from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
STEP = "- name: Block new Core provider coupling (P0 #5057)"
INVOKE = "python3 -m scripts.ci_provider_coupling_delta_gate"


def _verified_guard_step(workflow: str) -> bool:
    if workflow.count(STEP) != 1:
        return False
    # Restrict inspection to the guard's own YAML step: another unrelated
    # step cannot impersonate the gate's SHA binding or invocation.
    fragment = workflow.split(STEP, 1)[1].split("\n      - ", 1)[0]
    mandatory = (
        "if: github.event_name == 'pull_request'",
        "BASE_SHA: ${{ github.event.pull_request.base.sha }}",
        "HEAD_SHA: ${{ github.event.pull_request.head.sha }}",
        "set -euo pipefail",
        INVOKE,
        '--base-ref "$BASE_SHA" --head-ref "$HEAD_SHA"',
    )
    return all(value in fragment for value in mandatory)


def test_pr_ci_keeps_required_provider_guard_and_exact_commit_sha_binding():
    workflow = (ROOT / ".github/workflows/pr-ci.yml").read_text(encoding="utf-8")
    assert _verified_guard_step(workflow)
    assert re.search(r"(?m)^on:\s*$", workflow)
    assert re.search(r"(?m)^  pull_request:\s*$", workflow)


def test_guard_cannot_be_replaced_by_inert_or_foreign_step():
    workflow = (ROOT / ".github/workflows/pr-ci.yml").read_text(encoding="utf-8")
    assert _verified_guard_step(workflow)
    variants = (
        workflow.replace(INVOKE, "echo no-op", 1),
        workflow.replace(
            "BASE_SHA: ${{ github.event.pull_request.base.sha }}",
            "BASE_SHA: ${{ github.sha }}", 1,
        ),
        workflow.replace(
            "HEAD_SHA: ${{ github.event.pull_request.head.sha }}",
            "HEAD_SHA: ${{ github.sha }}", 1,
        ),
        workflow.replace(
            "if: github.event_name == 'pull_request'",
            "if: github.event_name == 'workflow_dispatch'", 1,
        ),
        workflow.replace(STEP, "- name: Disabled provider coupling guard", 1),
        workflow.replace(STEP, STEP + "\n      - " + STEP, 1),
    )
    assert all(not _verified_guard_step(case) for case in variants)
