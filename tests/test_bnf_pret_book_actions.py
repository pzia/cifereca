"""Collect offline BnF book-control safety contracts in Core CI."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret/tests/test_book_actions.py"
_spec = importlib.util.spec_from_file_location("bnf_book_action_signals_tests", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
BnfBookActionSignalsTests = _module.BnfBookActionSignalsTests
