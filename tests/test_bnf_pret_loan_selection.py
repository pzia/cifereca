"""Expose plugin read-only loan book choices to the Core offline gate."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret/tests/test_loan_selection.py"
_spec = importlib.util.spec_from_file_location("bnf_visible_loan_book_tests", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
BnfVisibleLoanBookSelectionTests = _module.BnfVisibleLoanBookSelectionTests
