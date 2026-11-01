"""#4048: no voluntary loan return can be initiated from book/loan GTK cards.

Static inspection is deliberate: tests catch a newly introduced callback even
when no supplier is connected, without executing ADEPT or provider HTTP.
Read-only historical and provenance projections are explicitly preserved.
"""
from __future__ import annotations

import ast
from pathlib import Path

GUI = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def _method(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == name)


def test_all_loan_detail_paths_absent_from_provider_and_adept_return_actions():
    owner = GUI / "detail_panel.py"
    show = _method(owner, "show_loan")
    body = ast.unparse(show)
    assert "confirm_return" not in body
    assert "return_adept_from_detail" not in body
    assert "Rendre maintenant" not in body
    assert "Restituer le prêt ADEPT" not in body
    assert "experimental_actions" not in body
    assert "adept_return_status_for_loan" in body  # read-only provenance
    assert "reconcile_adept_from_detail" in body  # read-only check
    assert "État du prêt ADEPT (lecture seule)" in body


def test_facet_buttons_are_filters_only_no_adept_return():
    owner = GUI / "detail_facets_controller.py"
    add_facets = _method(owner, "add_facets")
    body = ast.unparse(add_facets)
    assert "return_adept_acquisition" not in body
    assert "adept_return_for_artifact" not in body
    assert "runner.submit" not in body
    assert "window._facet_values" in body
    assert "window._facet_value_group" in body
