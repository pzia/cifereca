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
        self.assertEqual(len(mod.ALL), 17)

    def test_volume_only(self):
        self.assertEqual(mod.select(["e2e/diagnostics_volume_probe.py"]),
                         ("diagnostics-volume-1366",))

    def test_diagnostics_all_seven(self):
        self.assertEqual(set(mod.select(["src/plugins/diagnostics-page/page.py"])),
                         set(mod.DIAG))

    def test_youth_and_its_source_adapters(self):
        for path in ("src/plugins/youth-catalog/page.py",
                     "src/plugins/youth-source-milan/source.py",
                     "src/plugins/youth-source-new/source.py",
                     "simulation/youth_sources/fixture.py"):
            self.assertEqual(mod.select([path]), mod.YOUTH, path)

    def test_mainwindow_both_baselines_and_cross_domain(self):
        self.assertEqual(set(mod.select(["src/cifereca/gui/main_window.py"])),
                         set(mod.SHELL))
        self.assertEqual(set(mod.select(["src/cifereca/builtin_ui/library.py"])),
                         set(mod.SHELL))
        self.assertEqual(set(mod.select(["src/cifereca/app.py"])), set(mod.SHELL))

    def test_local_widget(self):
        self.assertEqual(mod.select(["src/cifereca/gui/book_widgets.py"]),
                         ("search-filter-p0", "book-detail-1366"))

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
