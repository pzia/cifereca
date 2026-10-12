"""No-network fail-closed publication tests for #1143."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

MOD = Path(__file__).resolve().parents[1] / "scripts/ci_publish_e2e_safe_summary.py"
spec = importlib.util.spec_from_file_location("safe_e2e_publication", MOD)
safe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safe)


class SafeE2EPublicationTests(unittest.TestCase):
    def setUp(self):
        self.names = frozenset({"startup", "search-filter-p0"})
        self.secret = "https://private.example/book.acsm?token=ALICE-SECRET"

    def test_synthetic_results_pass_without_paths_urls_or_private_diagnostics(self):
        rows = [
            {"scenario": "startup", "returncode": 0, "description": self.secret,
             "screenshot": "C:/Users/Alice/private.png", "stdout": self.secret,
             "stderr": "Cookie: secret", "trace": self.secret,
             "report": "/home/alice/.config/cifereca/report.json",
             "assertion_error": "Borrower Alice"},
            {"scenario": "search-filter-p0", "returncode": 1,
             "assertion_error": "user@example.org"},
        ]
        exported = safe.summarize(json.dumps(rows).encode(), self.names)
        self.assertEqual(exported["status"], "summarized")
        self.assertEqual((exported["total"], exported["passed"], exported["failed"]), (2, 1, 1))
        self.assertEqual(exported["scenarios"], [
            {"id": "startup", "outcome": "PASS"},
            {"id": "search-filter-p0", "outcome": "FAIL"},
        ])
        output = json.dumps(exported)
        for private in (self.secret, "Alice", "Cookie", "example.org", "/home/"):
            self.assertNotIn(private, output)

    def test_unknown_or_personal_scenario_fails_closed(self):
        for scenario in ("Alice", self.secret, "private-profile"):
            with self.subTest(scenario=scenario):
                payload = json.dumps([{"scenario": scenario, "returncode": 0}]).encode()
                self.assertEqual(safe.summarize(payload, self.names)["status"], "rejected")

    def test_noninteger_returncode_duplicate_missing_or_corrupt_json_rejected(self):
        for rows in (
            [{"scenario": "startup", "returncode": True}],
            [{"scenario": "startup", "returncode": -1}],
            [{"scenario": "startup", "returncode": 900}],
            [{"scenario": "startup", "returncode": 0},
             {"scenario": "startup", "returncode": 0}],
            [{"scenario": "startup", "assertion_error": "PRIVATE"}],
        ):
            self.assertEqual(safe.summarize(json.dumps(rows).encode(), self.names)["status"], "rejected")
        self.assertEqual(safe.summarize(b'{"secret":"HI"', self.names)["status"], "rejected")
        self.assertEqual(safe.summarize(b'x' * (128 * 1024 + 1), self.names)["status"], "rejected")

    def test_no_report_is_a_fixed_safe_empty_state(self):
        self.assertEqual(safe.summarize(None, self.names)["status"], "not_produced")

    def test_scenario_allowlist_is_ast_only_and_requires_literal_keys(self):
        source = 'SCENARIOS = {"startup": {"description": "PRIVATE"}, "search-filter-p0": {}}'
        self.assertEqual(safe.scenario_names(source), self.names)
        source = "SCENARIOS = {dangerous_call(): {}}"
        self.assertEqual(safe.scenario_names(source), frozenset())

    def test_publish_writes_only_sanitized_data_on_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "e2e.py"
            summary = root / "summary.json"
            output = root / "public/summary.json"
            script.write_text('SCENARIOS = {"startup": {}}', encoding="utf-8")
            summary.write_text(json.dumps([{"scenario": "startup", "returncode": 0,
                                            "report": self.secret}]), encoding="utf-8")
            self.assertEqual(safe.publish(script, summary, output)["status"], "summarized")
            self.assertNotIn(self.secret, output.read_text())
            summary.write_text(self.secret, encoding="utf-8")
            self.assertEqual(safe.publish(script, summary, output)["status"], "rejected")
            self.assertNotIn(self.secret, output.read_text())
            self.assertEqual(json.loads(output.read_text())["scenarios"], [])


if __name__ == "__main__":
    unittest.main()
