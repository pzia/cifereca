"""Collect the unregistered BnF Web contract in the existing Core CI gate.

Assertions stay in the provider package. No manifest is fabricated just to make
the plugin runner discover an offline adapter that isn't installable yet.
"""
import importlib.util
from pathlib import Path

_path = (Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret"
         / "tests/test_web_dom.py")
_spec = importlib.util.spec_from_file_location("bnf_web_dom_contract_tests", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
WebDOMTests = _module.WebDOMTests
