"""Collect the BnF plugin-only DOM probe contract in the Core offline gate."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret/tests/test_dom_probe.py"
_spec = importlib.util.spec_from_file_location("bnf_dom_probe_contract_tests", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
BnfDomProbeContractTests = _module.BnfDomProbeContractTests
