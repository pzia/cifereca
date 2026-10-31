"""Regression #2027: failed GTK probes cannot hide later independent verdicts."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/e2e-smoke.yml"
COMMAND = '/usr/bin/python3 e2e/run.py --scenario "$scenario" --out "e2e-artifacts/$scenario"'


def replay_shell_script():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    beginning = "          dbus-run-session -- bash -c '\n"
    ending = "\n          ' || status=$?"
    assert workflow.count(beginning) == 1
    fragment = workflow.split(beginning, 1)[1].split(ending, 1)[0]
    assert fragment.count(COMMAND) == 1
    # Only replace the real runner command in the test execution, not the
    # workflow: no GTK, Xvfb, network or customer data enters this fixture.
    return textwrap.dedent(fragment).replace(
        COMMAND, 'python3 "$TEST_RUNNER" "$scenario"'
    )


class ReplayAllE2EContract(unittest.TestCase):
    def test_real_shell_preserves_failure_and_runs_later_scenarios(self):
        script = replay_shell_script()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_runner = root / "fake_runner.py"
            fake_runner.write_text(
                "import os, sys\n"
                "from pathlib import Path\n"
                "with Path(os.environ['E2E_RECORD']).open('a') as out:\n"
                "    out.write(sys.argv[1] + '\\n')\n"
                "sys.exit(1 if sys.argv[1] == 'bad' else 0)\n",
                encoding="utf-8",
            )
            record = root / "record"
            for scenarios, expected_code in [
                ("good bad still-runs", 1),
                ("good another-good", 0),
            ]:
                record.unlink(missing_ok=True)
                result = subprocess.run(
                    ["bash", "-c", script],
                    env={
                        **os.environ,
                        "E2E_SCENARIOS": scenarios,
                        "TEST_RUNNER": str(fake_runner),
                        "E2E_RECORD": str(record),
                    },
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, expected_code, result.stderr)
                self.assertEqual(record.read_text().splitlines(), scenarios.split())
                self.assertEqual(result.stdout.count("::group::GTK/XTEST"), len(scenarios.split()))
                self.assertEqual(result.stdout.count("::endgroup::"), len(scenarios.split()))

    def test_real_gtk_gate_keeps_a_bounded_non_optional_budget(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        header = source.split("\njobs:\n", 1)[1].split("\n    steps:\n", 1)[0]
        self.assertIn("name: E2E GTK · core", header)
        self.assertIn("    timeout-minutes: 15", header)
        self.assertIn("  cancel-in-progress: true", source)
        self.assertNotIn("continue-on-error: true", source)

    def test_workflow_still_exports_verdicts_when_any_scenario_fails(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        assert "exit \"$failed\"" in source
        assert "' || status=$?" in source
        assert 'exit "$status"' in source
        assert 'if: always()' in source
        assert "scripts/ci-export-e2e-summary.py" in source


if __name__ == "__main__":
    unittest.main()
