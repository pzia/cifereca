"""Fail-closed contract for GitHub E2E evidence publication (#1143)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci-export-e2e-summary.py"
WORKFLOW = ROOT / ".github/workflows/e2e-smoke.yml"


def _module():
    spec = importlib.util.spec_from_file_location("ci_e2e_export", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SafeE2EExportTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.source = self.root / "summary.json"
        self.module = _module()

    def write(self, rows):
        self.source.write_text(json.dumps(rows), encoding="utf-8")

    def test_success_and_failure_only_export_allowlisted_fields(self):
        secret = "PRIVATE-BEARER-TOKEN-HOME-USER-ACSM-URL"
        self.write([
            {"scenario": "startup", "returncode": 0, "visual_change_ratio": 0.234567891,
             "description": secret, "stdout": "/home/private/" + secret,
             "stderr": secret, "screenshot": secret + ".png",
             "report": {"password": secret}, "trace": [{"cookie": secret}]},
            {"scenario": "core-navigation", "returncode": 1,
             "assertion_error": secret, "unmet_criteria": [secret]},
        ])
        dest = self.module.export(self.source, self.root / "published")
        published = dest.read_text(encoding="utf-8")
        self.assertNotIn(secret, published)
        self.assertNotIn("/home/private", published)
        self.assertEqual(json.loads(published), {
            "schema": "cifereca-e2e-public-verdict/v1",
            "results": [
                {"scenario": "startup", "status": "PASS", "returncode": 0,
                 "visual_change_ratio": 0.234568},
                {"scenario": "core-navigation", "status": "FAIL", "returncode": 1},
            ],
        })

    def test_invalid_scenario_boolean_code_and_nan_are_rejected(self):
        bad = (
            [{"scenario": "secret-private-user", "returncode": 0}],
            [{"scenario": "startup", "returncode": True}],
            [{"scenario": "startup", "returncode": 1, "visual_change_ratio": float("nan")}],
            [{"scenario": "startup", "returncode": 0, "visual_change_ratio": 2.0}],
            [{"scenario": "startup", "returncode": "0"}],
            {"scenario": "startup", "returncode": 0},
        )
        for i, rows in enumerate(bad):
            with self.subTest(i=i):
                self.write(rows)
                with self.assertRaises(ValueError):
                    self.module.export(self.source, self.root / f"unsafe-{i}")
                self.assertFalse((self.root / f"unsafe-{i}").exists())

    def test_input_symlink_oversized_and_invalid_json_refused_without_output(self):
        self.source.write_text("{secret:", encoding="utf-8")
        self.assertEqual(self.module.main(["--source", str(self.source), "--out-dir",
                                           str(self.root / "bad-json")]), 2)
        self.assertFalse((self.root / "bad-json").exists())
        self.source.write_bytes(b"X" * (self.module.MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            self.module.safe_summary(self.source)
        self.source.unlink()
        actual = self.root / "actual.json"
        actual.write_text('[{"scenario":"startup","returncode":0}]', encoding="utf-8")
        self.source.symlink_to(actual)
        with self.assertRaises(ValueError):
            self.module.safe_summary(self.source)

    def test_cannot_overwrite_preexisting_export_directory(self):
        self.write([{"scenario": "startup", "returncode": 0}])
        target = self.root / "published"
        target.mkdir()
        with self.assertRaises(FileExistsError):
            self.module.export(self.source, target)

    def test_workflow_publishes_only_generated_verdict_with_short_retention(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("scripts/ci-export-e2e-summary.py", content)
        self.assertIn("e2e-core-summary", content)
        self.assertIn("retention-days: 2", content)
        self.assertIn("cifereca-e2e-public/summary.json", content)
        self.assertNotIn("path: e2e-artifacts/", content)
        self.assertNotIn("cat e2e-artifacts/summary.json", content)
        self.assertNotIn("name: e2e-core-debug", content)


if __name__ == "__main__":
    unittest.main()
