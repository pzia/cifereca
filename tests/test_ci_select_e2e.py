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
        self.assertEqual(len(mod.ALL), 30)

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
        for path in ("src/cifereca/gui/maintenance_tools.py", "e2e/maintenance_tools_probe.py"):
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

    def test_diagnostics_all_seven(self):
        self.assertEqual(set(mod.select(["src/plugins/diagnostics-page/page.py"])),
                         set(mod.DIAG))

    def test_1599_youth_gtk_plugin_real_coverage_at_both_widths(self):
        assert {"youth-1366", "youth-1600"}.issubset(set(mod.YOUTH))
        for path in (
            "src/plugins/youth-catalog/youth_page.py",
            "src/plugins/youth-detail-enrichment/enrich.py",
            "e2e/youth_page_probe.py",
        ):
            selected = set(mod.select([path]))
            assert {"youth-1366", "youth-1600"}.issubset(selected), path

    def test_1599_youth_edition_and_library_navigation_1600_are_selected(self):
        expected = {
            "youth-editions-1366", "youth-editions-1600",
            "youth-library-search-1366", "youth-library-search-1600",
        }
        assert expected.issubset(set(mod.YOUTH))
        for path in (
            "e2e/youth_page_probe.py",
            "src/plugins/youth-catalog/youth_page.py",
            "src/plugins/youth-detail-enrichment/enrich.py",
        ):
            assert expected.issubset(set(mod.select([path]))), path

    def test_youth_and_its_source_adapters(self):
        for path in ("src/plugins/youth-catalog/page.py",
                     "src/plugins/youth-source-milan/source.py",
                     "src/plugins/youth-source-new/source.py",
                     "simulation/youth_sources/fixture.py"):
            self.assertEqual(mod.select([path]), mod.YOUTH, path)

    def test_1599_detail_plugin_changes_select_gtk_smoke(self):
        self.assertEqual(
            mod.select(["src/plugins/constellations/view_model.py"]),
            mod.CONSTELLATIONS,
        )
        self.assertEqual(
            mod.select(["src/plugins/recommendations/page.py"]),
            mod.RECOMMENDATIONS,
        )
        self.assertEqual(
            mod.select(["e2e/constellations_detail_probe.py"]),
            mod.CONSTELLATIONS_GTK,
        )
        self.assertEqual(
            mod.select(["e2e/recommendations_detail_probe.py"]),
            mod.RECOMMENDATIONS_GTK,
        )
        self.assertEqual(
            mod.select(["src/plugins/youth-detail-enrichment/enrich.py"]),
            mod.YOUTH,
        )
        self.assertEqual(set(mod.select([
            "src/plugins/constellations/view_model.py",
            "src/plugins/youth-detail-enrichment/enrich.py",
        ])), set(mod.CONSTELLATIONS + mod.YOUTH))

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

    def test_922_filter_ux_in_youth_scope(self):
        self.assertIn("youth-filter-help-1366", mod.select([
            "src/plugins/youth-catalog/youth_page.py",
        ]))
        self.assertIn("youth-filter-help-1366", mod.select(["e2e/youth_page_probe.py"]))

    def test_mainwindow_both_baselines_and_cross_domain(self):
        self.assertEqual(set(mod.select(["src/cifereca/gui/main_window.py"])),
                         set(mod.MAINWINDOW))
        self.assertEqual(set(mod.select(["src/cifereca/builtin_ui/library.py"])),
                         set(mod.SHELL))
        self.assertEqual(set(mod.select(["src/cifereca/app.py"])), set(mod.SHELL))

    def test_local_widget(self):
        self.assertEqual(mod.select(["src/cifereca/gui/book_widgets.py"]),
                         ("search-filter-p0",) + mod.BOOK_DETAIL)

    def test_1269_diagnostics_shared_probe_keeps_transitive_consumers(self):
        self.assertEqual(
            set(mod.select(["e2e/diagnostics_page_probe.py"])),
            set(mod.DIAG_SHARED),
        )
        self.assertEqual(len(mod.DIAG_SHARED), 9)
        self.assertEqual(set(mod.DIAG), set(mod.DIAG_SHARED) -
                         {"mainwindow-baseline-1366", "mainwindow-baseline-1600"})

    def test_1269_diagnostics_states_probe_keeps_navigation_and_baselines(self):
        selected = mod.select(["e2e/diagnostics_states_probe.py"])
        self.assertEqual(set(selected), set(mod.DIAG_STATES))
        self.assertEqual(len(selected), 5)

    def test_1269_query_model_includes_youth_query_state_consumers(self):
        selected = mod.select(["src/cifereca/gui/query_model.py"])
        self.assertEqual(set(selected), set(("search-filter-p0",) + mod.BOOK_DETAIL + mod.YOUTH))

    def test_1269_mainwindow_imports_cover_core_youth_diagnostics_navigation(self):
        selected = mod.select(["src/cifereca/gui/main_window.py"])
        self.assertEqual(set(selected), set(mod.MAINWINDOW))
        self.assertEqual(len(selected), 21)
        for name in mod.CORE + mod.YOUTH + (
            "diagnostics-navigation-1366", "diagnostics-navigation-slow-1366"
        ):
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
            "gtk-probe": "search_filter_probe.py",
            "gtk-detail": "book_detail_probe.py",
            "gtk-llm-settings": "llm_settings_probe.py",
            "gtk-maintenance": "maintenance_tools_probe.py",
            "gtk-diagnostics": "diagnostics_page_probe.py",
            "gtk-diagnostics-states": "diagnostics_states_probe.py",
            "gtk-diagnostics-structured": "diagnostics_structured_probe.py",
            "gtk-diagnostics-volume": "diagnostics_volume_probe.py",
            "gtk-diagnostics-navigation": "diagnostics_navigation_probe.py",
            "gtk-youth": "youth_page_probe.py",
            "gtk-constellations": "constellations_detail_probe.py",
            "gtk-recommendations": "recommendations_detail_probe.py",
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


    def test_shared_unknown_and_full_dispatch(self):
        for path in ("e2e/run.py", ".github/workflows/e2e-smoke.yml",
                     "scripts/ci-select-e2e.py", "e2e/unknown_probe.py",
                     "src/cifereca/gui/new_widget.py", "unknown/new_file.py"):
            self.assertEqual(mod.select([path]), mod.ALL, path)
        self.assertEqual(mod.select([]), mod.ALL)
        self.assertEqual(mod.select([], full=True), mod.ALL)

    def test_docs_only(self):
        self.assertEqual(mod.select(["e2e/README.md", "docs/e2e-guide.md"]), ())
        self.assertEqual(mod.select(["docs/readme.md", "e2e/diagnostics_volume_probe.py"]),
                         ("diagnostics-volume-1366",))

    def test_cross_domain_union_and_unknown_fail_closed(self):
        selected = mod.select(["src/plugins/diagnostics-page/page.py",
                               "src/plugins/youth-source-milan/adapter.py"])
        self.assertEqual(set(selected), set(mod.DIAG + mod.YOUTH))
        self.assertEqual(mod.select(["e2e/diagnostics_volume_probe.py", "unknown.cfg"]),
                         mod.ALL)

    def test_test_contract_full(self):
        self.assertEqual(mod.select(["tests/test_e2e_runner_contract.py"]), mod.ALL)

if __name__ == "__main__":
    unittest.main()
