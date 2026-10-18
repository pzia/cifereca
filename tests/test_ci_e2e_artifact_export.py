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
            [{"scenario": ["PRIVATE_LIST"], "returncode": 0}],
            [{"scenario": {"password": "PRIVATE_DICT"}, "returncode": 0}],
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


class SafeFailureDiagnosisTests(unittest.TestCase):
    def test_failed_baseline_exports_only_closed_stage_valid_line_and_bounded_duration(self):
        import ast
        module = _module()
        probe = ast.parse((ROOT / 'e2e/diagnostics_navigation_probe.py').read_text())
        line = next(n.lineno for n in ast.walk(probe) if isinstance(n, ast.FunctionDef) and n.name == 'later')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = 'mainwindow-baseline-1600'
            report = root / scenario / scenario / 'screen.json'
            report.parent.mkdir(parents=True)
            report.write_text(json.dumps({'stage':'P0_1905_numilog_inventory_click',
                'checkpoint_line':line, 'duration_seconds':50.1234,
                'error':'Timeout Diagnostics navigation 50s', 'cookie':'PRIVATE', 'path':'/home/PRIVATE'}))
            source = root / 'summary.json'
            source.write_text(json.dumps([{'scenario':scenario,'returncode':1,'report':'/home/PRIVATE'}]))
            result = module.safe_summary(source)['results'][0]
            self.assertEqual(result, {'scenario':scenario,'status':'FAIL','returncode':1,
                'last_stage':'P0_1905_numilog_inventory_click','checkpoint_line':line,
                'duration_seconds':50.12,'failure_kind':'scenario_timeout'})
            self.assertNotIn('PRIVATE', repr(result))

    def test_exact_failure_line_is_source_validated_without_traceback_or_payload(self):
        import ast
        module = _module()
        path = ROOT / "e2e/diagnostics_navigation_probe.py"
        probe = ast.parse(path.read_text())
        helper = next(n for n in probe.body
                      if isinstance(n, ast.FunctionDef) and n.name == "probe_failure_line")
        namespace = {"__file__": str(path)}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), str(path), "exec"), namespace)
        # A scheduled lambda calls a deeper probe function; the checkpoint
        # must not hide the assertion's actual source line.
        line = next(n.lineno for n in ast.walk(probe) if isinstance(n, ast.Assert))
        code = "\n" * (line - 2) + 'def nested():\n    assert False, "PRIVATE-TOKEN"\n'
        exec(compile(code, str(path), "exec"), namespace)
        try:
            (lambda: namespace["nested"]())()
        except AssertionError as exc:
            self.assertEqual(namespace["probe_failure_line"](exc), line)
            failure_line = namespace["probe_failure_line"](exc)
        try:
            raise RuntimeError("PRIVATE-EXTERNAL-URL")
        except RuntimeError as exc:
            self.assertIsNone(namespace["probe_failure_line"](exc))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = "mainwindow-baseline-1366"
            report = root / scenario / scenario / "screen.json"
            report.parent.mkdir(parents=True)
            source = root / "summary.json"
            source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
            for candidate in (failure_line, True, str(line), -1, 999999, helper.lineno):
                report.write_text(json.dumps({"failure_line": candidate,
                    "error": "PRIVATE-TOKEN", "traceback": "PRIVATE-EXTERNAL-URL"}))
                result = module.safe_summary(source)["results"][0]
                if type(candidate) is int and candidate == failure_line:
                    self.assertEqual(result["failure_line"], failure_line)
                else:
                    self.assertNotIn("failure_line", result)
                self.assertNotIn("PRIVATE", repr(result))

    def test_numilog_pointer_dialog_phase_verdict_is_bounded_and_privacy_safe(self):
        module = _module()
        stages = (
            "P0_1905_numilog_inventory_pointer_dispatch",
            "P0_1905_numilog_inventory_dialog_wait",
            "P0_1905_numilog_inventory_dialog_preflight",
            "P0_1905_numilog_inventory_dialog_ready",
        )
        probe = (ROOT / "e2e/diagnostics_navigation_probe.py").read_text(encoding="utf-8")
        for stage in stages:
            assert 'report["stage"] = "' + stage + '"' in probe
        assert [probe.index('report["stage"] = "' + stage + '"') for stage in stages] == sorted(
            probe.index('report["stage"] = "' + stage + '"') for stage in stages
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = "mainwindow-baseline-1366"
            report = root / scenario / scenario / "screen.json"
            report.parent.mkdir(parents=True)
            source = root / "summary.json"
            source.write_text(
                json.dumps([{"scenario": scenario, "returncode": 1,
                             "stderr": "PRIVATE-SIGNED-URL"}]), encoding="utf-8"
            )
            for stage in stages:
                report.write_text(
                    json.dumps({"stage": stage, "error": "PRIVATE-SIGNED-URL",
                                "html": "<account-private>", "cookie": "PRIVATE"}),
                    encoding="utf-8",
                )
                published = module.safe_summary(source)
                assert published["results"][0]["last_stage"] == stage
                assert published["results"][0]["failure_kind"] == "assertion_or_runtime"
                assert "PRIVATE" not in repr(published)
                assert "<account-private>" not in repr(published)

    def test_narrow_failure_categories_never_export_geometry_or_labels(self):
        module = _module()
        stages = (
            "P0_2027_narrow_header_minimum", "P0_2027_narrow_source_minimum",
            "P0_2027_narrow_scroll_minimum", "P0_2027_narrow_window_minimum",
            "P0_2027_narrow_allocation_pending",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = "mainwindow-baseline-1366"
            report = root / scenario / scenario / "screen.json"
            report.parent.mkdir(parents=True)
            source = root / "summary.json"
            source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
            for stage in stages:
                report.write_text(json.dumps({"stage": stage,
                    "sync_minimum_width_geometry": {"PRIVATE-ACCOUNT": 123456},
                    "error": "PRIVATE-TOKEN"}))
                row = module.safe_summary(source)["results"][0]
                self.assertEqual(row["last_stage"], stage)
                self.assertEqual(row["status"], "FAIL")
                self.assertNotIn("PRIVATE", repr(row))
                self.assertNotIn("123456", repr(row))

    def test_resize_wait_requires_actual_width_and_preserves_deadline_failure(self):
        import ast
        from types import SimpleNamespace
        path = ROOT / "e2e/diagnostics_navigation_probe.py"
        probe = ast.parse(path.read_text())
        node = next(n for n in ast.walk(probe)
                    if isinstance(n, ast.FunctionDef) and n.name == "wait_sync_narrow")
        width, now, pending, checked = [1280], [0.0], [], []
        def strict_allocations(large_font):
            checked.append(large_font)
            assert width[0] <= 760, "width assertion still enforced"
        namespace = {
            "w": SimpleNamespace(get_width=lambda: width[0]),
            "deadline": 4.0, "monotonic": lambda: now[0],
            "sync_narrow_allocations": strict_allocations,
            "later": lambda ms, callback: pending.append((ms, callback)),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
        wait = namespace["wait_sync_narrow"]
        wait()
        self.assertEqual(checked, [])
        self.assertEqual(pending[0][0], 80)
        now[0] = 0.8
        pending.pop()[1]()
        self.assertEqual(checked, [])
        width[0] = 760
        pending.pop()[1]()
        self.assertEqual(checked, [False])
        width[0], now[0] = 1280, 4.0
        with self.assertRaisesRegex(AssertionError, "width assertion still enforced"):
            wait()
        self.assertEqual(pending, [])

    def test_only_reviewed_watchdog_budgets_are_classified_as_timeouts(self):
        module = _module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = "mainwindow-baseline-1366"
            report = root / scenario / scenario / "screen.json"
            report.parent.mkdir(parents=True)
            source = root / "summary.json"
            source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
            for text, expected in (
                ("Timeout Diagnostics navigation 50s", "scenario_timeout"),
                ("Timeout Diagnostics navigation 75s", "scenario_timeout"),
                ("Timeout Diagnostics navigation PRIVATE", "assertion_or_runtime"),
                ({"token": "PRIVATE"}, "assertion_or_runtime"),
                (["PRIVATE"], "assertion_or_runtime"),
            ):
                report.write_text(json.dumps({"error": text}))
                row = module.safe_summary(source)["results"][0]
                self.assertEqual(row["failure_kind"], expected)
                self.assertNotIn("PRIVATE", repr(row))

    def test_unknown_strings_nonfinite_numbers_boolean_line_and_report_path_are_not_exported(self):
        module = _module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); scenario = 'mainwindow-baseline-1600'
            report = root / scenario / scenario / 'screen.json'; report.parent.mkdir(parents=True)
            report.write_text(json.dumps({'stage':'PRIVATE','checkpoint_line':True,
                'duration_seconds':float('inf'),'error':'PRIVATE'}))
            source = root / 'summary.json'; source.write_text(json.dumps([{'scenario':scenario,'returncode':1}]))
            result = module.safe_summary(source)['results'][0]
            self.assertEqual(result, {'scenario':scenario,'status':'FAIL','returncode':1,
                                      'failure_kind':'assertion_or_runtime'})
            report.unlink(); outside = root / 'outside.json'; outside.write_text('{"stage":"PRIVATE"}')
            report.symlink_to(outside)
            self.assertEqual(module.safe_summary(source)['results'][0],
                             {'scenario':scenario,'status':'FAIL','returncode':1})

    def test_missing_oversized_or_broken_report_does_not_hide_failure_or_publish_error(self):
        module = _module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); scenario = 'mainwindow-baseline-1600'
            source = root / 'summary.json'; source.write_text(json.dumps([{'scenario':scenario,'returncode':1}]))
            report = root / scenario / scenario / 'screen.json'; report.parent.mkdir(parents=True)
            expected = {'scenario':scenario,'status':'FAIL','returncode':1}
            self.assertEqual(module.safe_summary(source)['results'][0], expected)
            for content in ('PRIVATE', 'P' * (module.MAX_BYTES + 1)):
                report.write_text(content)
                self.assertEqual(module.safe_summary(source)['results'][0], expected)

if __name__ == "__main__":
    unittest.main()


class BulkFailureDiagnosisTests(unittest.TestCase):
    def test_bulk_failure_uses_exact_probe_and_never_publishes_geometry(self):
        import ast
        module = _module()
        probe = ast.parse((ROOT / "e2e/diagnostics_page_probe.py").read_text())
        line = next(n.lineno for n in ast.walk(probe) if isinstance(n, ast.Assert))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = "diagnostics-1366"
            report = root / scenario / scenario / "screen.json"
            report.parent.mkdir(parents=True)
            source = root / "summary.json"
            source.write_text(json.dumps([{"scenario":scenario,"returncode":1}]))
            for candidate in (3, True, "3", -1, 128, {"private":"PRIVATE"}):
                report.write_text(json.dumps({"stage":"P0_2140_narrow_window_minimum",
                    "failure_line":line, "minimum_row":candidate,
                    "row_minima":[9999], "window_minimum":9999,
                    "label":"PRIVATE", "error":"PRIVATE"}))
                out = module.safe_summary(source)["results"][0]
                self.assertEqual(out["failure_line"], line)
                self.assertEqual(out["last_stage"], "P0_2140_narrow_window_minimum")
                if type(candidate) is int and candidate == 3:
                    self.assertEqual(out["minimum_row"], 3)
                else:
                    self.assertNotIn("minimum_row", out)
                for private in ("row_minima", "window_minimum", "label", "error"):
                    self.assertNotIn(private, out)
                self.assertNotIn("PRIVATE", repr(out))
