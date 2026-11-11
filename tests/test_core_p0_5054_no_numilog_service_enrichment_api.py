"""P0 #5054: provider-specific metadata enrichment is outside Core Service."""
from pathlib import Path
import ast


def test_core_service_has_only_generic_explicit_enrichment():
    root = Path(__file__).resolve().parents[1]
    src = (root / "src/cifereca/services.py").read_text(encoding="utf-8")
    functions = {node.name for node in ast.walk(ast.parse(src))
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "enrich_numilog_search_result" not in functions
    assert "enrich_provider_search_result" in functions
    gui = (root / "src/cifereca/gui/numilog_enrichment_action.py").read_text(
        encoding="utf-8",
    )
    assert "enrich_provider_search_result" in gui
    assert "enrich_numilog_search_result" not in gui
