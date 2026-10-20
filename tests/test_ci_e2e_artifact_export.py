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


def test_diagnostics_timeout_wait_line_is_ast_validated_and_never_text():
    """Public E2E can locate a polling timeout without exposing GTK evidence."""
    import ast
    module = _module()
    tree = ast.parse((ROOT / "e2e/diagnostics_page_probe.py").read_text())
    line = next(node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "until")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = root / "diagnostics-1366" / "diagnostics-1366" / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": "diagnostics-1366",
                                      "returncode": 1}]))
        for raw, accepted in ((line, True), (True, False),
                              (str(line), False), (1000000000, False)):
            report.write_text(json.dumps({"wait_line": raw,
                "error": "PRIVATE_SIGNED_URL", "cookie": "PRIVATE_TOKEN",
                "predicate": "<private GUI text>"}))
            row = module.safe_summary(source)["results"][0]
            assert row.get("wait_line") == line if accepted else "wait_line" not in row
            assert "PRIVATE" not in repr(row)


def test_states_failure_source_lines_are_allowlisted_and_private_data_dropped():
    """#2139: the states E2E failure identifies code, never UI/secret text."""
    import ast
    module = _module()
    tree = ast.parse((ROOT / "e2e/diagnostics_states_probe.py").read_text())
    checkpoint = next(node.lineno for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == "d04")
    failure = next(node.lineno for node in ast.walk(tree)
                   if isinstance(node, ast.Assert) and node.lineno > checkpoint)
    wait = next(node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "until")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "diagnostics-states-1366"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        report.write_text(json.dumps({
            "checkpoint_line": checkpoint, "failure_line": failure,
            "wait_line": wait, "error": "PRIVATE_ACSM_URL",
            "secret": "PRIVATE_TOKEN", "stage": "PRIVATE_STAGE",
        }))
        row = module.safe_summary(source)["results"][0]
        assert row["checkpoint_line"] == checkpoint
        assert row["failure_line"] == failure
        assert row["wait_line"] == wait
        assert "PRIVATE" not in repr(row)
        report.write_text(json.dumps({
            "checkpoint_line": True, "failure_line": str(failure),
            "wait_line": 10**9, "error": "PRIVATE_ACSM_URL",
        }))
        row = module.safe_summary(source)["results"][0]
        assert "checkpoint_line" not in row
        assert "failure_line" not in row
        assert "wait_line" not in row
        assert "PRIVATE" not in repr(row)


def test_structured_diagnostics_reports_only_ast_verified_failure_lines():
    """#2139: structured GTK result may expose code coordinates, never secrets."""
    import ast
    module = _module()
    tree = ast.parse((ROOT / "e2e/diagnostics_structured_probe.py").read_text())
    checkpoint = next(n.lineno for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "readable")
    failure = next(n.lineno for n in ast.walk(tree)
                   if isinstance(n, ast.Assert))
    wait = next(n.lineno for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "until")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "diagnostics-structured-1366"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        report.write_text(json.dumps({
            "checkpoint_line": checkpoint, "failure_line": failure,
            "wait_line": wait, "error": "PRIVATE_SIGNED_TOKEN",
            "status": "FAIL", "debug": {"cookie": "PRIVATE"},
        }))
        safe = module.safe_summary(source)["results"][0]
        assert safe["checkpoint_line"] == checkpoint
        assert safe["failure_line"] == failure
        assert safe["wait_line"] == wait
        assert "PRIVATE" not in repr(safe)
        report.write_text(json.dumps({
            "checkpoint_line": False, "failure_line": str(failure),
            "wait_line": 999999, "error": "PRIVATE",
        }))
        invalid = module.safe_summary(source)["results"][0]
        assert "checkpoint_line" not in invalid
        assert "failure_line" not in invalid
        assert "wait_line" not in invalid


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


class NativeAbortDiagnosisTests(unittest.TestCase):
    def test_only_closed_native_markers_are_published(self):
        module = _module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = "diagnostics-large-1600"
            folder = root / scenario / scenario
            folder.mkdir(parents=True)
            source = root / "summary.json"
            source.write_text(json.dumps([{"scenario":scenario,"returncode":134}]))
            for marker, expected in (("malloc():", "allocator_abort_marker"),
                    ("Gtk:ERROR:", "gtk_assertion_marker"),
                    ("GLib:ERROR:", "glib_assertion_marker"),
                    ("Fatal Python error:", "python_abort_marker"),
                    ("unknown", "unclassified_native_abort")):
                (folder / "stderr.log").write_text("PRIVATE-TOKEN /home/private " + marker)
                out = module.safe_summary(source)["results"][0]
                self.assertEqual(out["native_failure_kind"], expected)
                self.assertNotIn("PRIVATE", repr(out))
                self.assertNotIn("/home/", repr(out))
                self.assertNotIn("stderr", out)
            source.write_text(json.dumps([{"scenario":scenario,"returncode":1}]))
            self.assertNotIn("native_failure_kind", module.safe_summary(source)["results"][0])

    def test_native_log_symlink_oversize_and_missing_are_not_read(self):
        module = _module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = "diagnostics-large-1600"
            folder = root / scenario / scenario
            folder.mkdir(parents=True)
            source = root / "summary.json"
            source.write_text(json.dumps([{"scenario":scenario,"returncode":134}]))
            log = folder / "stderr.log"
            def kind():
                return module.safe_summary(source)["results"][0]["native_failure_kind"]
            self.assertEqual(kind(), "unclassified_native_abort")
            secret = root / "private"
            secret.write_text("malloc(): PRIVATE")
            log.symlink_to(secret)
            self.assertEqual(kind(), "unclassified_native_abort")
            log.unlink()
            log.write_bytes(b"malloc():" + b"x" * module.MAX_BYTES)
            self.assertEqual(kind(), "unclassified_native_abort")


class SharedHelperFailureTests(unittest.TestCase):
    def test_only_reviewed_helper_source_and_its_ast_lines_cross_export(self):
        import ast
        module = _module()
        helper = ast.parse((ROOT / "e2e/activity_feedback_acceptance.py").read_text())
        checkpoint = next(n.lineno for n in ast.walk(helper)
                          if isinstance(n, ast.FunctionDef) and n.name == "close_dialog")
        failure = next(n.lineno for n in ast.walk(helper) if isinstance(n, ast.Assert))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = "mainwindow-baseline-1600"
            report = root / scenario / scenario / "screen.json"
            report.parent.mkdir(parents=True)
            source = root / "summary.json"
            source.write_text(json.dumps([{"scenario":scenario,"returncode":1}]))
            for tag in ("activity_feedback", "PRIVATE", {"source":"PRIVATE"}):
                report.write_text(json.dumps({"failure_source":tag, "failure_line":failure,
                    "checkpoint_source":tag, "checkpoint_line":checkpoint,
                    "error":"PRIVATE-TOKEN", "frame":"/home/PRIVATE"}))
                out = module.safe_summary(source)["results"][0]
                if tag == "activity_feedback":
                    self.assertEqual(out["failure_source"], tag)
                    self.assertEqual(out["failure_line"], failure)
                    self.assertEqual(out["checkpoint_source"], tag)
                    self.assertEqual(out["checkpoint_line"], checkpoint)
                else:
                    self.assertNotIn("failure_source", out)
                    self.assertNotIn("checkpoint_source", out)
                self.assertNotIn("PRIVATE", repr(out))

    def test_trace_location_walk_keeps_only_closed_source_identifier(self):
        import ast
        path = ROOT / "e2e/diagnostics_navigation_probe.py"
        tree = ast.parse(path.read_text())
        helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                      and n.name == "probe_failure_location")
        ns = {"__file__":str(path), "ROOT":ROOT}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), str(path), "exec"), ns)
        source = ROOT / "e2e/activity_feedback_acceptance.py"
        exec(compile('def fail():\n    assert False, "PRIVATE"\n', str(source), "exec"), ns)
        try:
            ns["fail"]()
        except AssertionError as exc:
            self.assertEqual(ns["probe_failure_location"](exc), ("activity_feedback", 2))
        try:
            raise RuntimeError("PRIVATE")
        except RuntimeError as exc:
            self.assertIsNone(ns["probe_failure_location"](exc))


def test_gtk_critical_only_publishes_closed_counts_never_raw_addresses():
    module = _module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "summary.json"
        source.write_text(json.dumps([{
            "scenario": "mainwindow-baseline-1366",
            "returncode": 1,
            "gtk_layout_critical": {
                "GtkStack": 87, "GtkRevealer": 57, "stream_truncated": False,
            },
            "stderr": "GtkStack 0xPRIVATE_POINTER /home/PRIVATE token=SECRET",
            "assertion_error": "PRIVATE",
        }]))
        safe = module.safe_summary(source)
        assert safe["results"][0]["gtk_layout_critical"] == {
            "GtkStack": 87, "GtkRevealer": 57, "stream_truncated": False,
        }
        assert "PRIVATE" not in repr(safe) and "SECRET" not in repr(safe)
        for bad in (
            {"GtkStack": "87", "GtkRevealer": 57, "stream_truncated": False},
            {"GtkStack": True, "GtkRevealer": 0, "stream_truncated": False},
            {"GtkStack": 1, "GtkRevealer": 1, "stream_truncated": False, "url": "PRIVATE"},
            {"GtkStack": 0, "GtkRevealer": 0, "stream_truncated": "PRIVATE"},
            {"GtkStack": -1, "GtkRevealer": 0, "stream_truncated": False},
        ):
            source.write_text(json.dumps([{
                "scenario": "startup", "returncode": 1,
                "gtk_layout_critical": bad,
            }]))
            try:
                module.safe_summary(source)
            except ValueError:
                pass
            else:
                raise AssertionError("Untrusted GTK geometry data published")


def test_2095_failed_kobo_audit_exports_only_closed_physical_click_state():
    """Explain sporadic XTEST failures without disclosing GTK/account evidence."""
    module = _module()
    secret = "PRIVATE_ACCOUNT_TOKEN_OR_URL"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        scenario = "mainwindow-baseline-1600"
        source = root / "summary.json"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source.write_text(json.dumps([{
            "scenario": scenario, "returncode": 1,
            "stderr": secret, "stdout": secret,
        }]), encoding="utf-8")
        evidence = {
            "clicked_signal": False,
            "broker_calls": 0,
            "subtitle_phase": "initial",
            "row_recreated": False,
            "preview_generation_changed": False,
            "identity_generation_changed": False,
            "page_rebuilt": False,
        }
        report.write_text(json.dumps({
            "kobo_audit_observation": evidence,
            "error": secret,
            "pointer_actions": [{"source_id": secret}],
        }), encoding="utf-8")
        safe = module.safe_summary(source)
        assert safe["results"][0]["kobo_audit_observation"] == evidence
        assert secret not in json.dumps(safe)
        # Wrong types, out-of-range counters and arbitrary metadata fail closed.
        for untrusted in (
            {**evidence, "clicked_signal": 1},
            {**evidence, "broker_calls": True},
            {**evidence, "broker_calls": 3},
            {**evidence, "subtitle_phase": secret},
            {**evidence, "row_recreated": 1},
            {**evidence, "preview_generation_changed": secret},
            {**evidence, "identity_generation_changed": None},
            {**evidence, "page_rebuilt": 1},
            {**evidence, "private": secret},
            [evidence],
        ):
            report.write_text(json.dumps({
                "kobo_audit_observation": untrusted,
                "error": secret,
            }), encoding="utf-8")
            redacted = module.safe_summary(source)["results"][0]
            assert "kobo_audit_observation" not in redacted
            assert secret not in json.dumps(redacted)
        # Passing scenarios cannot publish extra debug state.
        source.write_text(json.dumps([{
            "scenario": scenario, "returncode": 0,
        }]), encoding="utf-8")
        report.write_text(json.dumps({"kobo_audit_observation": evidence}),
                          encoding="utf-8")
        assert "kobo_audit_observation" not in module.safe_summary(source)["results"][0]
        # Other scenarios do not inherit these private diagnostic fields.
        source.write_text(json.dumps([{
            "scenario": "startup", "returncode": 1,
        }]), encoding="utf-8")
        assert "kobo_audit_observation" not in module.safe_summary(source)["results"][0]
