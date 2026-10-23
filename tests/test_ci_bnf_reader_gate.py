"""CI routing and no-empty/no-skipped acceptance proof invariants."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bnf_ci_gate', ROOT / 'scripts/ci_bnf_reader_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class BnfReaderGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / gate.PACKAGE
        (self.package / 'tests').mkdir(parents=True)

    def transport(self):
        (self.package / 'cifereca_bnf_pret_browser.py').touch()
        (self.package / 'tests/test_browser_chromium.py').touch()

    def test_harness_can_land_before_transport(self):
        self.assertFalse(gate.selected(['.github/workflows/pr-ci.yml'], self.root))

    def test_browser_parser_and_acceptance_changes_require_gate(self):
        self.transport()
        for path in ('cifereca_bnf_pret_browser.py', 'cifereca_bnf_pret_web.py',
                     'tests/test_browser_session.py', 'tests/test_browser_chromium.py',
                     'tests/extract_dom_contract.js'):
            self.assertTrue(gate.selected([gate.PACKAGE + path], self.root))

    def test_docs_other_provider_and_core_do_not_provision_browser(self):
        self.transport()
        for path in (gate.PACKAGE + 'WEB.md', 'README.md', 'src/plugins/provider-paris/a.py',
                     'src/cifereca/services.py'):
            self.assertFalse(gate.selected([path], self.root))

    def test_workflow_harness_and_manual_full_run_revalidate(self):
        self.transport()
        self.assertTrue(gate.selected(['.github/workflows/pr-ci.yml'], self.root))
        self.assertTrue(gate.selected(['scripts/ci_bnf_reader_gate.py'], self.root))
        self.assertTrue(gate.selected([], self.root, full=True))

    def test_missing_browser_acceptance_is_failure(self):
        self.transport()
        (self.package / 'tests/test_browser_chromium.py').unlink()
        with self.assertRaisesRegex(ValueError, '^bnf_chromium_acceptance_missing$'):
            gate.selected([gate.PACKAGE + 'cifereca_bnf_pret_browser.py'], self.root)

    def test_real_success_requires_nonempty_and_unskipped_results(self):
        result = unittest.TestResult()
        self.assertFalse(gate.successful(result))
        result.testsRun = 2
        self.assertTrue(gate.successful(result))
        result.skipped.append(('case', 'browser unavailable'))
        self.assertFalse(gate.successful(result))
        result.skipped.clear()
        result.failures.append(('case', 'failure'))
        self.assertFalse(gate.successful(result))


if __name__ == '__main__':
    unittest.main()
