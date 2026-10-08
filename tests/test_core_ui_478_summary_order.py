from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )


def test_shared_presentation_inserts_summary_after_essential():
    source = ast.unparse(_method("DetailPanel", "show_presentation"))

    assert "section.title == 'Essentiel'" in source
    assert "self._show_description(description)" in source

    essential = source.index("self._group(section.title, rows)")
    summary = source.index("self._show_description(description)")
    assert essential < summary


def test_search_detail_passes_description_into_shared_presentation():
    source = ast.unparse(_method("DetailPanel", "show_search"))

    assert "description=item.description" in source
    assert source.count("self._show_description(") == 0


def test_loaded_detail_prefers_detail_description_and_uses_same_ordering_path():
    source = ast.unparse(_method("DetailPanel", "show_item_detail"))

    assert "description=getattr(detail, 'description', None) or item.description" in source
    assert source.count("self._show_description(") == 0
