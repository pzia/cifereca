"""Expose the BnF reader-only pagination contract to the Core test gate."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret/tests/test_paging_contract.py"
_spec = importlib.util.spec_from_file_location("bnf_paging_contract_tests", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
BnfPagingContractTests = _module.BnfPagingContractTests
