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

    def test_book_detail_checkpoint_contract_matches_real_probe(self):
        # This unittest runs in E2E Smoke before GTK installation, including
        # exporter-only changes for which the Core test scope is empty.
        import ast
        probe = ast.parse((ROOT / "e2e/book_detail_probe.py").read_text(encoding="utf-8"))
        definitions = {node.name for node in ast.walk(probe)
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assignments = [node for node in probe.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name)
                               and target.id == "_PUBLIC_STAGE_CODES"
                               for target in node.targets)]
        self.assertEqual(len(assignments), 1)
        expression = assignments[0].value
        self.assertIsInstance(expression, ast.Call)
        self.assertIsInstance(expression.func, ast.Name)
        self.assertEqual(expression.func.id, "frozenset")
        self.assertEqual(len(expression.args), 1)
        self.assertFalse(expression.keywords)
        literal = ast.literal_eval(expression.args[0])
        self.assertIs(type(literal), list)
        self.assertTrue(all(type(value) is str for value in literal))
        self.assertLessEqual(len(literal), 64)
        self.assertEqual(len(literal), len(set(literal)))
        self.assertEqual(set(literal), self.module._BOOK_DETAIL_STAGES)
        self.assertLessEqual(set(literal) - {"activation", "callback_other"}, definitions)

    def test_notice_c_cover_and_six_view_failure_checkpoints_are_closed(self):
        scenario = "book-detail-1366"
        private = self.root / scenario / scenario / "screen.json"
        private.parent.mkdir(parents=True)
        self.write([{"scenario": scenario, "returncode": 1}])
        for cover, local in (
            ("seed", "setup"),
            ("cold_web_choices", "cold_search_detail"),
            ("offline_search_list", "offline_recommendations_list"),
            ("complete", "invalidated_n2_warm_n3"),
            ("complete", "epub_priority_cold"),
            ("complete", "epub_priority_warm"),
            ("complete", "epub_qualified_fallback"),
        ):
            with self.subTest(cover=cover, local=local):
                private.write_text(json.dumps({
                    "notice_c_cover_stage": cover, "local_c_stage": local,
                    "error": "PRIVATE", "title": "PRIVATE",
                }), encoding="utf-8")
                result = self.module.safe_summary(self.source)["results"][0]
                self.assertEqual(result["book_detail_notice_c_cover_stage"], cover)
                self.assertEqual(result["book_detail_local_c_stage"], local)
                self.assertNotIn("PRIVATE", repr(result))
        for bad in ("PRIVATE", "cold_web_choices\\nPRIVATE", True, 17, None, {"a": "b"}):
            private.write_text(json.dumps({
                "notice_c_cover_stage": bad, "local_c_stage": bad,
            }), encoding="utf-8")
            result = self.module.safe_summary(self.source)["results"][0]
            self.assertNotIn("book_detail_notice_c_cover_stage", result)
            self.assertNotIn("book_detail_local_c_stage", result)
        self.write([{"scenario": scenario, "returncode": 0}])
        private.write_text(json.dumps({
            "notice_c_cover_stage": "seed", "local_c_stage": "setup",
        }), encoding="utf-8")
        result = self.module.safe_summary(self.source)["results"][0]
        self.assertNotIn("book_detail_notice_c_cover_stage", result)
        self.assertNotIn("book_detail_local_c_stage", result)

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


def test_book_detail_pillow_capture_phase_is_strictly_allowlisted():
    module = _module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "book-detail-1366"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        for value in ("imagegrab", "display_geometry", "png_write",
                      "PRIVATE_X11_ERROR", True, 7, None):
            report.write_text(json.dumps({
                "capture_failure_phase": value,
                "error": "PRIVATE_SIGNED_IMAGE_URL", "account": "PRIVATE",
            }))
            result = module.safe_summary(source)["results"][0]
            if value in {"imagegrab", "display_geometry", "png_write"}:
                assert result["book_detail_capture_failure_phase"] == value
            else:
                assert "book_detail_capture_failure_phase" not in result
            assert "PRIVATE" not in repr(result)
        # Only failed GTK probes may disclose the diagnostic category.
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 0}]))
        report.write_text(json.dumps({"capture_failure_phase": "imagegrab"}))
        assert "book_detail_capture_failure_phase" not in module.safe_summary(source)["results"][0]


def test_book_detail_callback_line_is_ast_validated_and_excludes_private_details():
    import ast
    module = _module()
    tree = ast.parse((ROOT / "e2e/book_detail_probe.py").read_text())
    call = next(node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "later")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "book-detail-1600"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        for line, accepted in ((call, True), (True, False),
                               (str(call), False), (10**9, False)):
            report.write_text(json.dumps({
                "public_stage": "callback_other",
                "callback_line": line,
                "error": "PRIVATE_SIGNED_URL",
                "secret": "PRIVATE_COOKIE",
            }))
            result = module.safe_summary(source)["results"][0]
            assert result["book_detail_last_stage"] == "callback_other"
            if accepted:
                assert result["book_detail_callback_line"] == call
            else:
                assert "book_detail_callback_line" not in result
            assert "PRIVATE" not in repr(result)


def test_book_detail_1700_closed_stage_exports_without_private_values():
    module = _module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "book-detail-1700"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        report.write_text(json.dumps({
            "public_stage": "component_overflow",
            "error": "PRIVATE_SIGNED_URL", "token": "PRIVATE_TOKEN",
        }))
        row = module.safe_summary(source)["results"][0]
        assert row["book_detail_last_stage"] == "component_overflow"
        assert "PRIVATE" not in repr(row)
        report.write_text(json.dumps({"public_stage": "PRIVATE_STAGE"}))
        assert "book_detail_last_stage" not in module.safe_summary(source)["results"][0]


def test_bulk_refresh_timeout_exports_only_strict_fixture_flags():
    module = _module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "diagnostics-large-1600"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        flags = {
            "refresh_mapped": True,
            "refresh_sensitive": False,
            "fixture_enabled": True,
            "broker_queried": False,
        }
        report.write_text(json.dumps({
            "stage": "P0_2140_bulk_fixture",
            "bulk_refresh_observation": flags,
            "error": "PRIVATE_JOURNAL", "token": "PRIVATE_TOKEN",
        }))
        row = module.safe_summary(source)["results"][0]
        assert row["bulk_refresh_observation"] == flags
        assert "PRIVATE" not in repr(row)
        for bad in (
            {"refresh_mapped": 1, "refresh_sensitive": False, "fixture_enabled": True, "broker_queried": False},
            {**flags, "private_label": "PRIVATE"},
            {"refresh_mapped": True},
        ):
            report.write_text(json.dumps({
                "stage": "P0_2140_bulk_fixture",
                "bulk_refresh_observation": bad,
            }))
            assert "bulk_refresh_observation" not in module.safe_summary(source)["results"][0]
        report.write_text(json.dumps({
            "stage": "PRIVATE_STAGE", "bulk_refresh_observation": flags,
        }))
        assert "bulk_refresh_observation" not in module.safe_summary(source)["results"][0]


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

    def test_maintenance_failure_location_and_intermediate_checks_are_bounded(self):
        import ast

        module = _module()
        probe = ast.parse((ROOT / "e2e/maintenance_tools_probe.py").read_text(
            encoding="utf-8"))
        line = next(node.lineno for node in ast.walk(probe)
                    if isinstance(node, ast.Assert))
        secret = "PRIVATE-BEARER-TOKEN /home/private"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for scenario in ("maintenance-tools-1280", "maintenance-tools-1600"):
                folder = root / scenario / scenario
                folder.mkdir(parents=True)
                source = root / "summary.json"
                report = folder / "screen.json"
                source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
                data = {
                    "checks": {"real_tools_route_and_action": True,
                               "cache_progress_attached_to_real_tool_row": True,
                               "busy_sync_refuses_preview_before_scan_or_delete": 1,
                               secret: True},
                    "maintenance_failure_line": line,
                    "error": secret, "failure_stack": secret,
                }
                report.write_text(json.dumps(data))
                out = module.safe_summary(source)["results"][0]
                self.assertEqual(out["maintenance_last_safe_checkpoint"], "maintenance_progress_row")
                self.assertEqual(out["maintenance_failure_line"], line)
                self.assertNotIn(secret, repr(out))
                for invalid in (True, str(line), -1, 999999, secret):
                    report.write_text(json.dumps(dict(data, maintenance_failure_line=invalid)))
                    out = module.safe_summary(source)["results"][0]
                    self.assertNotIn("maintenance_failure_line", out)
                    self.assertEqual(out["maintenance_last_safe_checkpoint"],
                                     "maintenance_progress_row")
                checks = dict(data["checks"], busy_sync_refuses_preview_before_scan_or_delete=True)
                report.write_text(json.dumps(dict(data, checks=checks)))
                out = module.safe_summary(source)["results"][0]
                self.assertEqual(out["maintenance_last_safe_checkpoint"], "maintenance_busy_guard")
                source.write_text(json.dumps([{"scenario": scenario, "returncode": 0}]))
                out = module.safe_summary(source)["results"][0]
                self.assertNotIn("maintenance_failure_line", out)
                self.assertNotIn("maintenance_last_safe_checkpoint", out)

    def test_maintenance_probe_reports_innermost_own_frame_without_gtk_import(self):
        import ast

        path = ROOT / "e2e/maintenance_tools_probe.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "_maintenance_failure_line")
        namespace = {}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), str(path), "exec"), namespace)
        exec("def inner():\n    raise AssertionError('PRIVATE')\ndef outer():\n    inner()\n",
             namespace)
        try:
            namespace["outer"]()
        except AssertionError as exc:
            self.assertEqual(namespace["_maintenance_failure_line"](exc),
                             namespace["inner"].__code__.co_firstlineno + 1)
        else:
            self.fail("expected fixture failure")
        self.assertIsNone(namespace["_maintenance_failure_line"](AssertionError("PRIVATE")))

    def test_maintenance_sigabrt_exports_only_last_closed_gtk_phase(self):
        module = _module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = "maintenance-tools-1600"
            folder = root / scenario / scenario
            folder.mkdir(parents=True)
            source = root / "summary.json"
            source.write_text(json.dumps([{"scenario": scenario, "returncode": 134}]))
            log = folder / "stderr.log"
            log.write_bytes(
                b"PRIVATE-ACCOUNT-TOKEN\n"
                b"CIFERECA_E2E_GTK_PHASE:maint_window_start\n"
                b"CIFERECA_E2E_GTK_PHASE:maint_declaration_dialog_open\n"
                b"PRIVATE-URL https://private.invalid/secret\n"
            )
            out = module.safe_summary(source)["results"][0]
            self.assertEqual(out["maintenance_native_last_phase"],
                             "maint_declaration_dialog_open")
            self.assertEqual(out["native_failure_kind"], "unclassified_native_abort")
            for boundary in ("maint_transfer_start", "maint_transfer_done"):
                log.write_bytes(f"CIFERECA_E2E_GTK_PHASE:{boundary}\n".encode())
                self.assertEqual(
                    module.safe_summary(source)["results"][0]["maintenance_native_last_phase"],
                    boundary,
                )
            log.write_bytes(
                b"CIFERECA_E2E_GTK_PHASE:maint_declaration_dialog_open\n"
            )
            self.assertNotIn("PRIVATE", repr(out))
            self.assertNotIn("private.invalid", repr(out))

            # Exact, complete, reviewed literals only; no injection through
            # a public marker-shaped line or an otherwise passing scenario.
            for invalid in (
                b"CIFERECA_E2E_GTK_PHASE:maint_private_token\n",
                b"CIFERECA_E2E_GTK_PHASE:maint_route PRIVATE\n",
                b"prefix CIFERECA_E2E_GTK_PHASE:maint_route\n",
                b"CIFERECA_E2E_GTK_PHASE:maint_route_suffix\n",
            ):
                log.write_bytes(invalid)
                self.assertNotIn(
                    "maintenance_native_last_phase",
                    module.safe_summary(source)["results"][0],
                )
            source.write_text(json.dumps([{"scenario":scenario, "returncode":0}]))
            log.write_bytes(b"CIFERECA_E2E_GTK_PHASE:maint_route\n")
            self.assertNotIn("maintenance_native_last_phase",
                             module.safe_summary(source)["results"][0])
            source.write_text(json.dumps([{"scenario":"startup","returncode":134}]))
            self.assertNotIn("maintenance_native_last_phase",
                             module.safe_summary(source)["results"][0])


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


def test_gtk_replay_logs_only_closed_scenario_verdicts_and_preserves_failure():
    """Human-readable CI diagnosis must not expose raw GTK or account data."""
    source = WORKFLOW.read_text(encoding="utf-8")
    assert 'echo "CIFERECA_E2E_VERDICT:PASS:$scenario"' in source
    assert 'echo "CIFERECA_E2E_VERDICT:FAIL:$scenario"' in source
    assert 'if /usr/bin/python3 e2e/run.py --scenario "$scenario"' in source
    assert 'failed=1' in source
    assert 'exit "$failed"' in source
    assert 'exit "$status"' in source
    assert 'cat e2e-artifacts/summary.json' not in source
    assert 'cat e2e-artifacts/' not in source
    assert 'name: e2e-core-debug' not in source
    assert 'uses: actions/upload-artifact@v7.0.2' in source
    assert 'name: e2e-core-summary' in source
    assert 'if-no-files-found: ignore' in source
    assert 'retention-days: 2' in source


def test_plugin_scroll_failure_exports_only_closed_fixture_checkpoint():
    module = _module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "plugin-manager-scroll-1366"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        stages = (
            "initial_scroll", "first_toggle", "first_grab_focus",
            "first_scroll_request", "first_toggle_geometry", "first_toggle_scroll",
            "first_toggle_set_inactive", "first_toggle_callback",
            "first_geometry", "first_scroll",
            "first_anchor", "first_switch", "first_focus", "second_geometry",
        )
        for stage in stages:
            report.write_text(json.dumps({
                "plugin_scroll_checkpoint": stage,
                "error": "PRIVATE-CREDENTIAL-URL",
            }))
            out = module.safe_summary(source)["results"][0]
            assert out["plugin_scroll_last_check"] == stage
            assert "PRIVATE" not in repr(out)
        for invalid in (True, 12, None, "PRIVATE-CREDENTIAL-URL"):
            report.write_text(json.dumps({"plugin_scroll_checkpoint": invalid}))
            out = module.safe_summary(source)["results"][0]
            assert "plugin_scroll_last_check" not in out
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 0}]))
        report.write_text(json.dumps({"plugin_scroll_checkpoint": "first_focus"}))
        assert "plugin_scroll_last_check" not in module.safe_summary(source)["results"][0]
        source.write_text(json.dumps([{"scenario": "startup", "returncode": 1}]))
        assert "plugin_scroll_last_check" not in module.safe_summary(source)["results"][0]


def test_p0_5376_bulk_focus_public_evidence_is_closed_boolean_only():
    module = _module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "diagnostics-large-1600"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        evidence = {
            "button_mapped": True,
            "button_sensitive": True,
            "toplevel_active": False,
            "button_focused": False,
            "toplevel_focus_is_button": False,
        }
        report.write_text(json.dumps({
            "stage": "P0_2140_bulk_fixture",
            "bulk_focus_observation": evidence,
            "private_exception": "TOKEN-ACCOUNT-PRIVATE",
        }))
        result = module.safe_summary(source)["results"][0]
        assert result["bulk_focus_observation"] == evidence
        assert "TOKEN-ACCOUNT-PRIVATE" not in repr(result)
        for malformed in (
            {**evidence, "button_mapped": 1},
            {**evidence, "button_sensitive": "PRIVATE"},
            {**evidence, "extra": False},
            [evidence],
        ):
            report.write_text(json.dumps({
                "stage": "P0_2140_bulk_fixture",
                "bulk_focus_observation": malformed,
            }))
            assert "bulk_focus_observation" not in module.safe_summary(source)["results"][0]
        report.write_text(json.dumps({
            "stage": "P0_2140_wait_57_errors",
            "bulk_focus_observation": evidence,
        }))
        assert "bulk_focus_observation" not in module.safe_summary(source)["results"][0]
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 0}]))
        report.write_text(json.dumps({
            "stage": "P0_2140_bulk_fixture",
            "bulk_focus_observation": evidence,
        }))
        assert "bulk_focus_observation" not in module.safe_summary(source)["results"][0]


def test_p0_5376_diagnostics_file_selector_timeout_exports_only_closed_booleans():
    module = _module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "diagnostics-1366"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        state = {
            "dialog_visible": True,
            "selected_file_present": False,
            "selected_file_matches_fixture": False,
            "current_name_matches_fixture": True,
        }
        report.write_text(json.dumps({
            "capture_file_selection_timeout": state,
            "error": "PRIVATE-TOKEN /home/private",
        }))
        result = module.safe_summary(source)["results"][0]
        assert result["capture_file_selection_timeout"] == state
        assert "PRIVATE-TOKEN" not in repr(result)
        assert "/home/private" not in repr(result)
        for invalid in (
            {**state, "dialog_visible": 1},
            {**state, "selected_file_present": "PRIVATE"},
            {**state, "secret": False},
            [state],
        ):
            report.write_text(json.dumps({"capture_file_selection_timeout": invalid}))
            assert "capture_file_selection_timeout" not in module.safe_summary(source)["results"][0]
        report.write_text(json.dumps({"capture_file_selection_timeout": state}))
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 0}]))
        assert "capture_file_selection_timeout" not in module.safe_summary(source)["results"][0]
        source.write_text(json.dumps([{"scenario": "diagnostics-large-1600", "returncode": 1}]))
        assert "capture_file_selection_timeout" not in module.safe_summary(source)["results"][0]


class DiagnosticsFocusAndFileChooserPrivacyTests(unittest.TestCase):
    """Execute the closed-evidence guards under unittest discover in CI."""

    def test_bulk_focus_allowlist_is_enforced(self):
        test_p0_5376_bulk_focus_public_evidence_is_closed_boolean_only()

    def test_file_chooser_allowlist_is_enforced(self):
        test_p0_5376_diagnostics_file_selector_timeout_exports_only_closed_booleans()



def test_bnf_covers_failure_exports_only_closed_synthetic_stage():
    """Never publish private GTK screenshots, exceptions or provider text."""
    module = _module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "bnf-covers-1366"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        # The workflow passes --out e2e-artifacts/<scenario> to run.py,
        # and run.py appends <scenario>. Never read a one-level decoy.
        shallow = root / scenario / "screen.json"
        shallow.write_text(json.dumps({"last_stage": "startup"}))
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        for value, accepted in (
            ("search_late_detail_rebind", True),
            ("embedded_loan_list_over_remote", True),
            ("embedded_loan_detail_over_remote", True),
            ("embedded_loan_list_over_supplier", True),
            ("embedded_loan_detail_over_supplier", True),
            ("late_library_loan_list_cover", True),
            ("late_library_loan_detail_cover", True),
            ("remote_loan_detail_cover", True),
            ("startup", True),
            ("PRIVATE_SIGNED_URL", False),
            (123, False),
            (True, False),
        ):
            report.write_text(json.dumps({
                "last_stage": value,
                "error": "PRIVATE_BORROWER_TOKEN",
                "user_path": "/home/private/loan",
            }))
            row = module.safe_summary(source)["results"][0]
            if accepted:
                assert row["bnf_covers_last_stage"] == value
            else:
                assert "bnf_covers_last_stage" not in row
            assert "PRIVATE" not in repr(row)
            assert "/home/private" not in repr(row)

        source.write_text(json.dumps([{"scenario": scenario, "returncode": 0}]))
        report.write_text(json.dumps({"last_stage": "embedded_loan_detail_over_remote"}))
        assert "bnf_covers_last_stage" not in module.safe_summary(source)["results"][0]
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        report.unlink()
        report.symlink_to(root / "secret.json")
        assert "bnf_covers_last_stage" not in module.safe_summary(source)["results"][0]



def test_bnf_probe_checkpoints_are_saved_before_asynchronous_gtk_wait():
    import ast
    source = (ROOT / "e2e/bnf_cover_probe.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    condition = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "await_condition"
    )
    lines = [
        ast.unparse(stmt) for stmt in condition.body
        if isinstance(stmt, (ast.Assign, ast.Expr))
    ]
    assert 'report[\'last_stage\'] = label' in lines
    assert 'save()' in lines
    assert lines.index('report[\'last_stage\'] = label') < lines.index('save()')



def test_book_detail_960_local_c_checkpoint_exports_only_closed_phase():
    module = _module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "book-detail-960"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        for value in ("cold_library_list", "offline_recent_detail", "complete",
                      "PRIVATE_RECORD", True, {}, None):
            report.write_text(json.dumps({
                "local_c_stage": value, "error": "PRIVATE_PATH",
                "source": "PRIVATE_URL",
            }))
            result = module.safe_summary(source)["results"][0]
            if value in ("cold_library_list", "offline_recent_detail", "complete"):
                assert result["book_detail_local_c_stage"] == value
            else:
                assert "book_detail_local_c_stage" not in result
            assert "PRIVATE" not in repr(result)
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 0}]))
        report.write_text(json.dumps({"local_c_stage": "complete"}))
        assert "book_detail_local_c_stage" not in module.safe_summary(source)["results"][0]



def test_bnf_borrower_c_checkpoints_are_closed_and_failure_only():
    module = _module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "bnf-covers-1366"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        for stage in ("borrower_c_setup", "borrower_c_seed", "borrower_c_cold_loans_list",
                      "borrower_c_offline_history_detail", "borrower_c_complete",
                      "borrower_c_PRIVATE_URL", None, {}):
            report.write_text(json.dumps({"last_stage": stage, "error": "PRIVATE_PATH"}))
            result = module.safe_summary(source)["results"][0]
            if type(stage) is str and stage in {
                "borrower_c_setup", "borrower_c_seed", "borrower_c_cold_loans_list",
                "borrower_c_offline_history_detail", "borrower_c_complete",
            }:
                assert result["bnf_covers_last_stage"] == stage
            else:
                assert "bnf_covers_last_stage" not in result
            assert "PRIVATE" not in repr(result)
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 0}]))
        report.write_text(json.dumps({"last_stage": "borrower_c_complete"}))
        assert "bnf_covers_last_stage" not in module.safe_summary(source)["results"][0]


def test_completed_six_view_cover_receipt_exports_only_closed_numbers():
    module = _module()
    import copy
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scenario = "book-detail-1366"
        report = root / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True)
        source = root / "summary.json"
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 0}]))
        def phase(cold):
            return {"schema": "cifereca.cover-six-views/1",
                    "fixture": "one_c_six_views_mock_transport",
                    "image_gets_total": 1,
                    "route_to_observed_texture_ms": {route: 3.125 for route in module._COVER_ROUTES},
                    "n1": {"n1_selected_count": 1, "n1_PRIVATE": 123},
                    "n2": {"n2_hit": 11},
                    "n3": {"download_ok": int(cold), "download_failed": 0,
                           "preview_worker_created_recent_count": int(cold),
                           "preview_worker_hit_recent_count": 11 + int(not cold),
                           "preview_worker_fallback_recent_count": 0,
                           "preview_worker_hit_recent_p95_ms": .123,
                           "preview_worker_PRIVATE": "secret"},
                    "n4": {"ui_applied": 12, "ui_failed": 0, "ui_decode_recent_count": 12,
                           "ui_decode_recent_p95_ms": 1.234, "ui_decode_PRIVATE": "secret"},
                    "url": "PRIVATE"}
        data = {"local_c_stage": "complete",
                "checks": {"P0_4980_same_c_six_physical_views_restart_offline": True},
                "notice_c_cover_metrics_cold": phase(True),
                "notice_c_cover_metrics_offline": phase(False),
                "path": "/home/PRIVATE"}
        def read(value):
            report.write_text(json.dumps(value))
            return module.safe_summary(source)["results"][0]
        assert "notice_c_catalogue_http_fallback" not in read(data)
        for value in (False, 1, "PRIVATE", None, {"status": True}):
            bad = copy.deepcopy(data)
            bad["checks"]["P0_4980_catalogue_http_fallback_real_gtk"] = value
            assert "notice_c_catalogue_http_fallback" not in read(bad)
        data["checks"]["P0_4980_catalogue_http_fallback_real_gtk"] = True
        for phase in ("cold", "offline"):
            data["checks"]["P0_3104_retained_private_series_real_gtk_" + phase] = True
        for phase in ("cold", "offline"):
            for value in (False, 1, "PRIVATE", None, {}):
                bad = copy.deepcopy(data)
                bad["checks"]["P0_3104_retained_private_series_real_gtk_" + phase] = value
                assert "notice_c_private_series_cold_offline" not in read(bad)
        assert "notice_c_narrow_six_views_cold_offline" not in read(data)
        for phase_name in ("cold", "offline"):
            data["checks"]["P0_3684_narrow_six_views_" + phase_name] = True
            data["notice_c_narrow_context_" + phase_name] = {
                route: {"returned": True, "reopened_exact_selection": True,
                        "native_focus_returned": True, "window_width": 960,
                        "widget": "PRIVATE"}
                for route in ("library", "recent", "search", "loans",
                              "history", "recommendations")
            }
        for phase_name in ("cold", "offline"):
            for bad_value in (False, 1, "PRIVATE", None, {}):
                bad = copy.deepcopy(data)
                bad["checks"]["P0_3684_narrow_six_views_" + phase_name] = bad_value
                assert "notice_c_narrow_six_views_cold_offline" not in read(bad)
                for key in ("returned", "reopened_exact_selection", "native_focus_returned"):
                    bad = copy.deepcopy(data)
                    bad["notice_c_narrow_context_" + phase_name]["library"][key] = bad_value
                    assert "notice_c_narrow_six_views_cold_offline" not in read(bad)
            bad = copy.deepcopy(data)
            del bad["notice_c_narrow_context_" + phase_name]["history"]
            assert "notice_c_narrow_six_views_cold_offline" not in read(bad)
            bad = copy.deepcopy(data)
            bad["notice_c_narrow_context_" + phase_name]["PRIVATE"] = {}
            assert "notice_c_narrow_six_views_cold_offline" not in read(bad)
        result = read(data)
        assert result["notice_c_narrow_six_views_cold_offline"] is True
        assert "notice_c_narrow_context_cold" not in result
        assert "window_width" not in repr(result)
        assert result["notice_c_private_series_cold_offline"] is True
        assert result["notice_c_catalogue_http_fallback"] is True
        safe = result["notice_c_cover_metrics"]
        assert len(safe["cold"]["route_to_observed_texture_ms"]) == 12
        assert safe["cold"]["n3"]["download_ok"] == 1
        assert safe["offline"]["n3"]["download_ok"] == 0
        assert safe["cold"]["n4"]["ui_decode_recent_p95_ms"] == 1.23
        assert safe["cold"]["n3"]["preview_worker_created_recent_count"] == 1
        assert safe["offline"]["n3"]["preview_worker_created_recent_count"] == 0
        assert safe["offline"]["n3"]["preview_worker_hit_recent_p95_ms"] == .12
        bad = copy.deepcopy(data)
        bad["notice_c_cover_metrics_cold"]["n3"]["preview_worker_hit_recent_p95_ms"] = "PRIVATE"
        assert "notice_c_cover_metrics" not in read(bad)
        assert "PRIVATE" not in repr(result)
        for field, value in (("ui_applied", True), ("ui_decode_recent_p95_ms", float("nan")),
                             ("ui_failed", 1), ("ui_decode_recent_count", -1)):
            bad = copy.deepcopy(data)
            bad["notice_c_cover_metrics_cold"]["n4"][field] = value
            assert "notice_c_cover_metrics" not in read(bad)
        for change in ("stage", "check", "route", "offline"):
            bad = copy.deepcopy(data)
            if change == "stage":
                bad["local_c_stage"] = "cold_library_list"
            elif change == "check":
                bad["checks"]["P0_4980_same_c_six_physical_views_restart_offline"] = 1
            elif change == "route":
                bad["notice_c_cover_metrics_cold"]["route_to_observed_texture_ms"]["PRIVATE"] = 1
            else:
                del bad["notice_c_cover_metrics_offline"]
            assert "notice_c_cover_metrics" not in read(bad)
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
        assert "notice_c_private_series_cold_offline" not in read(data)
        assert "notice_c_narrow_six_views_cold_offline" not in read(data)
        assert "notice_c_cover_metrics" not in read(data)
        assert "notice_c_catalogue_http_fallback" not in read(data)
        for value in ("catalogue_http_fallback", "catalogue_http_fallback_PRIVATE"):
            bad = copy.deepcopy(data)
            bad["local_c_stage"] = value
            row = read(bad)
            if value == "catalogue_http_fallback":
                assert row["book_detail_local_c_stage"] == value
            else:
                assert "book_detail_local_c_stage" not in row
            assert "notice_c_catalogue_http_fallback" not in row
            assert "PRIVATE" not in repr(row)
        source.write_text(json.dumps([{"scenario": scenario, "returncode": 0}]))
        read(data)
        other = root / "private.json"
        report.rename(other)
        report.symlink_to(other)
        row = module.safe_summary(source)["results"][0]
        assert "notice_c_cover_metrics" not in row
        assert "notice_c_catalogue_http_fallback" not in row
