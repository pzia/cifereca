"""Expose the plugin-only visible selection contract to the Core CI gate."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret/tests/test_visible_selection.py"
_spec = importlib.util.spec_from_file_location("bnf_visible_selection_contract_tests", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
BnfVisibleSelectionTests = _module.BnfVisibleSelectionTests
