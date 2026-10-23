"""Collect the browser adapter contract while the provider is unregistered."""
import importlib.util
from pathlib import Path

_path = (Path(__file__).resolve().parents[1] / 'src/plugins/provider-bnf-pret'
         / 'tests/test_browser_session.py')
_spec = importlib.util.spec_from_file_location('bnf_browser_session_contract_tests', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
BrowserSessionTests = _module.BrowserSessionTests
