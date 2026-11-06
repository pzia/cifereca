"""P0-safe and frugal GTK path routing for issue #1236."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("select_e2e", ROOT / "scripts/ci-select-e2e.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

class SelectionTests(unittest.TestCase):
    def test_exact_runner_scenarios(self):
        spec = importlib.util.spec_from_file_location("runner", ROOT / "e2e/run.py")
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        self.assertEqual(set(mod.ALL), set(runner.SCENARIOS))
        self.assertEqual(len(mod.ALL), 35)
        self.assertIn("maintenance-tools-920", mod.ALL)
        self.assertIn("maintenance-tools-1024", mod.ALL)
        self.assertIn("maintenance-tools-1728", mod.ALL)
        self.assertEqual(mod.select(["e2e/maintenance_tools_probe.py"]), mod.MAINT)
        self.assertIn("public-fault-search-1366", mod.ALL)
        self.assertIn("kobo-fault-search-1366", mod.ALL)
        self.assertIn("book-detail-1300", mod.ALL)
        self.assertEqual(runner.SCENARIOS["book-detail-1300"]["height"], 768)
        self.assertTrue(runner.SCENARIOS["book-detail-1300"]["geometry_1300"])

    def test_p1_4118_bnf_real_gtk_cover_gate_is_routed_to_producer_and_ui(self):
        self.assertEqual(mod.BNF_COVERS, ("bnf-covers-1366",))
        for path in (
            "e2e/bnf_cover_probe.py",
            "src/plugins/provider-bnf-pret/cifereca_bnf_pret_web.py",
            "src/plugins/provider-bnf-pret/cifereca_bnf_pret.py",
        ):
            self.assertEqual(mod.select([path]), mod.BNF_COVERS)
        for path in (
            "src/cifereca/gui/book_widgets.py",
            "src/cifereca/gui/cover_loader.py",
            "src/cifereca/gui/query_model.py",
            "src/cifereca/gui/main_window.py",
        ):
            self.assertIn("bnf-covers-1366", mod.select([path]))

    def test_4093_capture_gtk_smoke_is_selected_for_gui_entry_and_probe(self):
        self.assertEqual(
            mod.select(["e2e/capture_session_gtk_probe.py"]),
            mod.CAPTURE_GTK,
        )
        self.assertEqual(
            mod.CAPTURE_GTK, ("capture-session-gtk-1366",),
        )
        self.assertIn(
            "capture-session-gtk-1366",
            mod.select(["src/cifereca/app.py"]),
        )

    def test_2800_optimizer_native_handoff_selected_for_real_plugin_changes(self):
        self.assertEqual(
            mod.select(["src/plugins/epub_optimizer/cifereca_epub_optimizer_plugin.py"]),
            mod.OPTIMIZER_GTK,
        )
        self.assertEqual(
            mod.select(["src/plugins/epub_optimizer/tests/test_mounted_artifact_handoff_p1_2800.py"]),
            mod.OPTIMIZER_GTK,
        )
        self.assertEqual(
            mod.select(["e2e/optimizer_handoff_probe.py"]),
            mod.OPTIMIZER_GTK,
        )
        self.assertEqual(set(mod.OPTIMIZER_GTK), {
            "optimizer-handoff-1366", "optimizer-handoff-1600",
        })

    def test_1127_settings_actions_select_real_gtk_at_both_widths(self):
        for path in ("src/cifereca/gui/settings_ai_actions.py",
                     "src/cifereca/gui/settings_ai_section.py",
                     "src/cifereca/gui/settings_lifetime.py",
                     "e2e/llm_settings_probe.py"):
            self.assertEqual(mod.select([path]), mod.LLM, path)
        self.assertTrue(set(mod.LLM).issubset(set(mod.MAINWINDOW)))

    def test_p0_sync_core_changes_select_both_real_source_gtk_baselines(self):
        for path in (
            "src/cifereca/brokers.py",
            "src/cifereca/core/source_materialization.py",
            "src/cifereca/core/numilog_materialization_startup.py",
            "src/cifereca/core/numilog_materialization_checkpoint.py",
            "src/cifereca/core/numilog_direct_acquisition.py",
            "src/cifereca/core/loan_local_preparation.py",
            "src/cifereca/core/loan_provider_delivery.py",
            "src/cifereca/core/sync_coordination.py",
        ):
            self.assertEqual(mod.select([path]), mod.SYNC_CORE, path)
        self.assertEqual(
            set(mod.select([
                "src/cifereca/brokers.py",
                "src/cifereca/gui/sync_controller.py",
            ])),
            set(mod.ALL),
        )  # Unknown direct GUI owner remains fail-closed to full E2E.

    def test_1358_maintenance_safety_selected_at_both_widths(self):
        for path in ("src/cifereca/gui/maintenance_tools.py", "src/cifereca/gui/maintenance_result_view.py", "e2e/maintenance_tools_probe.py"):
            self.assertEqual(mod.select([path]), mod.MAINT)
        self.assertTrue(set(mod.MAINT).issubset(set(mod.MAINWINDOW)))

    def test_1492_line_progress_dependencies_select_maintenance_at_both_widths(self):
        paths = ("src/cifereca/gui/maintenance_line_progress.py",
                 "src/cifereca/gui/maintenance_line_widgets.py")
        for path in paths:
            self.assertEqual(mod.select([path]), mod.MAINT, path)
        self.assertEqual(mod.select(list(paths) + [
            "src/cifereca/gui/maintenance_tools.py",
            "e2e/maintenance_tools_probe.py",
        ]), mod.MAINT)
        self.assertEqual(set(mod.select(list(paths) + [
            "src/cifereca/gui/main_window.py",
        ])), set(mod.MAINWINDOW))

    def test_volume_only(self):
        self.assertEqual(mod.select(["e2e/diagnostics_volume_probe.py"]),
                         ("diagnostics-volume-1366",))

    def test_home_plugin_changes_select_existing_native_baselines_at_both_widths(self):
        for path in ("src/plugins/home-page/cifereca_home.py",
                     "src/plugins/home-page/cifereca-plugin.json",
                     "src/plugins/home-page/tests/test_owned_loan_summary_1867.py"):
            self.assertEqual(mod.select([path]), mod.HOME, path)
        self.assertEqual(set(mod.HOME), {
            "mainwindow-baseline-1366", "mainwindow-baseline-1600",
        })
        self.assertTrue(set(mod.HOME).issubset(set(mod.ALL)))

    def test_diagnostics_all_seven(self):
        for path in ("src/plugins/diagnostics-page/page.py",
                     "src/cifereca/core/diagnostic_numilog_projection.py"):
            self.assertEqual(set(mod.select([path])), set(mod.DIAG))

    def test_1599_detail_plugin_changes_select_gtk_smoke(self):
        self.assertEqual(mod.select(["src/plugins/constellations/view_model.py"]), mod.CONSTELLATIONS)
        self.assertEqual(mod.select(["src/plugins/recommendations/page.py"]), mod.RECOMMENDATIONS)
        self.assertEqual(mod.select(["e2e/constellations_detail_probe.py"]), mod.CONSTELLATIONS_GTK)
        self.assertEqual(mod.select(["e2e/recommendations_detail_probe.py"]), mod.RECOMMENDATIONS_GTK)

    def test_1599_constellations_real_gtk_both_widths(self):
        assert set(mod.CONSTELLATIONS_GTK) == {
            "constellations-detail-1366", "constellations-detail-1600",
        }
        assert set(mod.CONSTELLATIONS_GTK).issubset(set(mod.CONSTELLATIONS))
        for path in (
            "src/plugins/constellations/constellations_page.py",
            "src/plugins/constellations/constellations.py",
        ):
            assert set(mod.CONSTELLATIONS_GTK).issubset(set(mod.select([path])))

    def test_1599_recommendations_gtk_both_widths(self):
        assert set(mod.RECOMMENDATIONS_GTK) == {
            "recommendations-detail-1366", "recommendations-detail-1600",
        }
        assert set(mod.RECOMMENDATIONS_GTK).issubset(set(mod.RECOMMENDATIONS))
        for path in (
            "src/plugins/recommendations/recommendations.py",
            "src/plugins/recommendations/recommendation_engine.py",
        ):
            assert set(mod.RECOMMENDATIONS_GTK).issubset(set(mod.select([path])))

    def test_mainwindow_both_baselines_and_cross_domain(self):
        self.assertEqual(set(mod.select(["src/cifereca/gui/main_window.py"])),
                         set(mod.MAINWINDOW))
        self.assertEqual(set(mod.select(["src/cifereca/builtin_ui/library.py"])),
                         set(mod.SHELL))
        self.assertEqual(set(mod.select(["src/cifereca/app.py"])), set(mod.SHELL))

    def test_local_widget(self):
        self.assertEqual(mod.select(["src/cifereca/gui/book_widgets.py"]),
                         ("search-filter-p0",) + mod.BOOK_DETAIL + mod.RECOMMENDATIONS_GTK + mod.BNF_COVERS)
        self.assertEqual(mod.select(["src/cifereca/gui/cover_loader.py"]),
                         mod.BOOK_DETAIL + mod.RECOMMENDATIONS_GTK + mod.BNF_COVERS)

    def test_1269_diagnostics_shared_probe_keeps_transitive_consumers(self):
        self.assertEqual(
            set(mod.select(["e2e/diagnostics_page_probe.py"])),
            set(mod.DIAG_SHARED),
        )
        self.assertEqual(len(mod.DIAG_SHARED), 10)
        self.assertEqual(set(mod.DIAG), set(mod.DIAG_SHARED) -
                         {"mainwindow-baseline-1366", "mainwindow-baseline-1600"})

    def test_1269_diagnostics_states_probe_keeps_navigation_and_baselines(self):
        selected = mod.select(["e2e/diagnostics_states_probe.py"])
        self.assertEqual(set(selected), set(mod.DIAG_STATES))
        self.assertEqual(len(selected), 5)

    def test_1269_query_model_keeps_book_detail_consumers(self):
        selected = mod.select(["src/cifereca/gui/query_model.py"])
        self.assertEqual(set(selected), set(("search-filter-p0",) + mod.BOOK_DETAIL + mod.BNF_COVERS))

    def test_1269_mainwindow_imports_cover_core_diagnostics_navigation(self):
        selected = mod.select(["src/cifereca/gui/main_window.py"])
        self.assertEqual(set(selected), set(mod.MAINWINDOW))
        for name in mod.CORE + ("diagnostics-navigation-1366", "diagnostics-navigation-slow-1366"):
            self.assertIn(name, selected)

    def test_1269_probe_import_graph_never_silently_omits_a_consumer(self):
        """Guard future direct and transitive dependencies of GTK probes.

        Every import of a sibling E2E probe or a real GUI module must select
        the consumer scenario. Traversal is unnecessary: if every direct
        graph edge is covered, the selection also covers transitive edges.
        """
        import ast
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "runner_for_graph", ROOT / "e2e/run.py"
        )
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        entrypoints = {
            "gtk-capture-session": "capture_session_gtk_probe.py",
            "gtk-probe": "search_filter_probe.py",
            "gtk-detail": "book_detail_probe.py",
            "gtk-llm-settings": "llm_settings_probe.py",
            "gtk-maintenance": "maintenance_tools_probe.py",
            "gtk-diagnostics": "diagnostics_page_probe.py",
            "gtk-diagnostics-raw-export": "diagnostics_raw_export_probe.py",
            "gtk-diagnostics-states": "diagnostics_states_probe.py",
            "gtk-diagnostics-structured": "diagnostics_structured_probe.py",
            "gtk-diagnostics-volume": "diagnostics_volume_probe.py",
            "gtk-diagnostics-navigation": "diagnostics_navigation_probe.py",
            "gtk-constellations": "constellations_detail_probe.py",
            "gtk-recommendations": "recommendations_detail_probe.py",
            "gtk-bnf-covers": "bnf_cover_probe.py",
        }
        for scenario, descriptor in runner.SCENARIOS.items():
            filename = entrypoints.get(descriptor.get("runner"))
            if filename is None:
                continue  # startup/core-navigation use the capture runner
            script = ROOT / "e2e" / filename
            tree = ast.parse(script.read_text(encoding="utf-8"))
            imported = []
            for node in ast.walk(tree):
                names = ([node.module] if isinstance(node, ast.ImportFrom)
                         else [alias.name for alias in node.names]
                         if isinstance(node, ast.Import) else [])
                imported.extend(name for name in names if name)
            for name in imported:
                if name.startswith("cifereca.gui."):
                    relative = "src/" + name.replace(".", "/") + ".py"
                elif "." not in name:
                    relative = "e2e/" + name + ".py"
                else:
                    continue
                if not (ROOT / relative).is_file():
                    continue
                self.assertIn(
                    scenario, mod.select([relative]),
                    f"{scenario} uses {relative} via {filename}",
                )


    def test_1354_release_snapshot_is_not_a_gtk_dependency(self):
        # Version snapshots are synchronized for an explicit release, not at
        # every plugin patch. Other unknown runtime paths stay fail-closed.
        snapshot = "src/cifereca/plugin-release.json"
        self.assertEqual(mod.select([snapshot]), ())
        self.assertEqual(mod.select([
            snapshot,
            "src/plugins/recommendations/recommendations.py",
            "src/plugins/recommendations/cifereca-plugin.json",
        ]), mod.RECOMMENDATIONS)
        self.assertEqual(mod.select([snapshot, "unknown/runtime.py"]), mod.ALL)

    def test_1354_workflow_uses_merge_base_not_unrelated_main_commits(self):
        workflow = (ROOT / ".github/workflows/e2e-smoke.yml").read_text(encoding="utf-8")
        self.assertIn('git diff --name-only "$BASE_SHA...$HEAD_SHA"', workflow)
        self.assertNotIn('git diff --name-only "$BASE_SHA" "$HEAD_SHA"', workflow)

    def test_shared_unknown_and_full_dispatch(self):
        for path in ("e2e/run.py", ".github/workflows/e2e-smoke.yml",
                     "scripts/ci-select-e2e.py", "e2e/unknown_probe.py",
                     "src/cifereca/gui/new_widget.py", "unknown/new_file.py"):
            self.assertEqual(mod.select([path]), mod.ALL, path)
        self.assertEqual(mod.select([]), mod.ALL)
        self.assertEqual(mod.select([], full=True), mod.ALL)

    def test_path_filter_contract_uses_synthetic_gate_not_gtk_replay(self):
        # The workflow still starts to check selector and publication policy;
        # Core PR CI runs the static path assertions directly.
        path = "tests/test_e2e_ci_paths.py"
        self.assertEqual(mod.select([path]), ())
        self.assertEqual(mod.select([path, "docs/notes.md"]), ())
        self.assertEqual(
            mod.select([path, "src/cifereca/gui/main_window.py"]),
            mod.MAINWINDOW,
        )
        self.assertEqual(mod.select([path, "unknown/runtime.py"]), mod.ALL)
        self.assertEqual(mod.select([path], full=True), mod.ALL)
        workflow = (ROOT / ".github/workflows/e2e-smoke.yml").read_text(encoding="utf-8")
        self.assertIn("      - 'tests/test_e2e_ci_paths.py'\n", workflow)
        self.assertIn("      - name: Test E2E scenario selector", workflow)
        self.assertIn("      - name: Validate privacy-safe E2E publication policy", workflow)

    def test_docs_only(self):
        self.assertEqual(mod.select(["e2e/README.md", "docs/e2e-guide.md"]), ())
        self.assertEqual(mod.select(["docs/readme.md", "e2e/diagnostics_volume_probe.py"]),
                         ("diagnostics-volume-1366",))

    def test_cross_domain_union_and_unknown_fail_closed(self):
        selected = mod.select(["src/plugins/diagnostics-page/page.py",
                               "src/plugins/recommendations/page.py"])
        self.assertEqual(set(selected), set(mod.DIAG + mod.RECOMMENDATIONS))
        self.assertEqual(mod.select(["e2e/diagnostics_volume_probe.py", "unknown.cfg"]), mod.ALL)

    def test_test_contract_full(self):
        self.assertEqual(mod.select(["tests/test_e2e_runner_contract.py"]), mod.ALL)

if __name__ == "__main__":
    unittest.main()
