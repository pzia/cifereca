"""CI/CD: full GTK/XTEST selection remains a bounded, mandatory PR gate."""
from pathlib import Path
import re


WORKFLOW = (Path(__file__).resolve().parents[1] /
            ".github/workflows/e2e-smoke.yml")


def test_full_e2e_job_has_bounded_budget_for_thirty_one_scenarios():
    source = WORKFLOW.read_text(encoding="utf-8")
    jobs = source.split("\njobs:\n", 1)[1]
    header = jobs.split("\n    steps:\n", 1)[0]
    assert "name: E2E GTK · core" in header
    budget = re.findall(r"(?m)^    timeout-minutes: ([0-9]+)$", header)
    assert budget == ["15"]
    assert "  cancel-in-progress: true" in source
    assert "continue-on-error: true" not in source


def test_e2e_replays_all_selected_cases_and_keeps_a_failing_verdict():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert 'for scenario in $E2E_SCENARIOS; do' in source
    assert 'failed=1' in source
    assert 'exit "$failed"' in source
    assert "scripts/ci-export-e2e-summary.py" in source
