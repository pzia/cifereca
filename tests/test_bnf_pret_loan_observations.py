"""Collect BnF non-destructive observation tests in the Core gate."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret/tests/test_loan_observations.py"
_spec = importlib.util.spec_from_file_location("bnf_reader_observation_contract_tests", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
BnfLoanObservationsTests = _module.BnfLoanObservationsTests
