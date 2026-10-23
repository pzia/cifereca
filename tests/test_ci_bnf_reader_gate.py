"""BnF HTTPX routing must never provision a browser."""
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
        (self.package / 'cifereca_bnf_pret_http.py').touch()
        (self.root / gate.TEST).touch()

    def test_harness_can_land_before_transport(self):
        self.assertFalse(gate.selected(['.github/workflows/pr-ci.yml'], self.root))

    def test_http_parser_and_acceptance_changes_require_gate(self):
        self.transport()
        for path in ('cifereca_bnf_pret_http.py', 'cifereca_bnf_pret_web.py',
                     'tests/test_http_provider.py', 'cifereca-plugin.json', 'WEB.md'):
            self.assertTrue(gate.selected([gate.PACKAGE + path], self.root))

    def test_other_provider_and_core_do_not_select_gate(self):
        self.transport()
        for path in ('README.md', 'src/plugins/provider-paris/a.py', 'src/cifereca/services.py'):
            self.assertFalse(gate.selected([path], self.root))

    def test_workflow_harness_and_manual_full_run_revalidate(self):
        self.transport()
        self.assertTrue(gate.selected(['.github/workflows/pr-ci.yml'], self.root))
        self.assertTrue(gate.selected(['scripts/ci_bnf_reader_gate.py'], self.root))
        self.assertTrue(gate.selected([], self.root, full=True))

    def test_missing_http_acceptance_is_failure(self):
        self.transport()
        (self.root / gate.TEST).unlink()
        with self.assertRaisesRegex(ValueError, '^bnf_http_acceptance_missing$'):
            gate.selected([gate.PACKAGE + 'cifereca_bnf_pret_http.py'], self.root)

    def test_acceptance_uses_shared_python_without_browser_install(self):
        workflow = (ROOT / '.github/workflows/pr-ci.yml').read_text()
        step = workflow.split('- name: BnF HTTPX reader acceptance', 1)[1].split('\n      - name:', 1)[0]
        self.assertIn('python scripts/ci_bnf_reader_gate.py run', step)
        self.assertNotIn('playwright', step)
        self.assertNotIn('chromium', step)
        self.assertNotIn('pip install', step)
        self.assertNotIn('CIFERECA_BNF_CHROMIUM_TEST', workflow)

    def test_plugin_only_http_acceptance_installs_core_sqlite_dependencies(self):
        workflow = (ROOT / '.github/workflows/pr-ci.yml').read_text()
        step = workflow.split('- name: Install plugin-only test dependencies', 1)[1].split('      - name:', 1)[0]
        self.assertIn('steps.scope.outputs.bnf_reader', step)
        self.assertIn('pip install --disable-pip-version-check -e .', step)

if __name__ == '__main__':
    unittest.main()
