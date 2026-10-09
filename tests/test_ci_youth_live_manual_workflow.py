"""Static safety gate for the explicitly operator-triggered youth live check.

Do not execute this workflow from tests: it intentionally contacts public websites
only after an operator gives the opt-in in GitHub Actions.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/youth-source-live-qualification.yml"


def test_public_https_workflow_is_dispatch_only_and_confirmation_gated():
    source = WORKFLOW.read_text(encoding="utf-8")
    triggers = source.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    assert "workflow_dispatch:" in triggers
    assert not any(("\n  " + automatic + ":") in triggers for automatic in
                   ("push", "pull_request", "schedule", "workflow_run"))
    assert "default: false" in triggers
    assert "type: boolean" in triggers
    assert "if: ${{ inputs.confirm_live == true }}" in source
    assert "SOURCE: ${{ inputs.source }}" in source
    assert "booktrust|milan) ;;" in source


def test_live_workflow_is_bounded_and_keeps_nonpass_evidence():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in source
    assert "timeout-minutes: 4" in source
    assert "--age 8 --limit 2 --timeout 6" in source
    assert "--confirm-live > youth-live-evidence/result.json" in source
    assert source.count("if: always()") == 2
    assert "actions/upload-artifact@v4" in source
    assert "retention-days: 7" in source
