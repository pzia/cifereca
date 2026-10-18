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
    assert "milan|ecole-loisirs) ;;" in source


def test_live_workflow_is_bounded_and_keeps_nonpass_evidence():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in source
    assert "timeout-minutes: 4" in source
    assert '--age "$AGE" --limit 2 --timeout 6' in source
    assert 'if [[ "$SOURCE" == "ecole-loisirs" ]]; then AGE=3; fi' in source
    assert '--confirm-live > "$raw" 2>/dev/null' in source
    assert "trap 'rm -f \"$raw\"' EXIT" in source
    assert "python - \"$raw\" <<'PY' > youth-live-evidence/result.json" in source
    assert "module.public_report(raw_report)" in source
    assert 'raw_report.get("source") != os.environ["SOURCE"]' in source
    assert 'safe.get("requests_count", 0) < 1' in source
    assert 'safe.get("qualified_notice_count", 0) < 1' in source
    assert source.count("if: always()") == 2
    assert "actions/upload-artifact@v4" in source
    assert "retention-days: 2" in source
