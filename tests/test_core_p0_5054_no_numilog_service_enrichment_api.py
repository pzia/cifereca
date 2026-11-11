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
    # The supplier-specific GUI module is removed, including its dynamic
    # import. Rendering is selected by a generic installed enrichment ABI.
    assert not (root / "src/cifereca/gui/numilog_enrichment_action.py").exists()
    gui = (root / "src/cifereca/gui/search_details.py").read_text(
        encoding="utf-8",
    )
    assert "_add_explicit_enrichment_action" in gui
    assert "numilog_enrichment_action" not in gui
    assert 'source_provider", None) in {"numilog", "numilog-catalog"}' not in gui
