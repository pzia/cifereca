"""P0 #5055: candidate reasons are provider-owned, not a Core BnF helper."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bnf_candidate_classifier_has_no_core_owner_or_fallback():
    root = ROOT / "src/cifereca"
    diagnostic = (root / "core/diagnostic_bnf_loan_action.py").read_text(
        encoding="utf-8",
    )
    service = (root / "services.py").read_text(encoding="utf-8")
    tree = ast.parse(diagnostic)
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "provider_reason"
        for node in tree.body
    )
    assert "from ..plugin_api import ProviderConfigurationError, ProviderError" not in diagnostic
    assert "loan_stage_candidate_reason" in service
    assert "classify_candidate_error=lambda error: candidate_reason(" in service
    assert "or not isfunction(candidate_reason)" in service
    assert "provider_reason" not in service


def test_provider_exposes_closed_candidate_reason_without_core_import():
    plugin = (ROOT / "src/plugins/provider-bnf-pret/cifereca_bnf_pret.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(plugin)
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "BnfProviderSession")
    assert any(isinstance(node, ast.FunctionDef)
               and node.name == "loan_stage_candidate_reason"
               for node in cls.body)
    assert "bnf_loan_token_unavailable" in plugin
