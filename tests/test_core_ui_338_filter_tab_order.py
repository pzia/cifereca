from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUERY_CONTROLS = ROOT / "src/cifereca/gui/query_controls.py"


def _method_source(class_name: str, method_name: str) -> str:
    tree = ast.parse(QUERY_CONTROLS.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_query_filter_flowbox_children_are_not_tab_stops():
    source = _method_source("QueryControls", "__init__")
    assert "child = widget.get_parent()" in source
    assert "isinstance(child, Gtk.FlowBoxChild)" in source
    assert "child.set_focusable(False)" in source
    assert source.count("insert_filter(") >= 6
