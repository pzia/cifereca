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


def test_query_filters_have_no_intermediate_flowbox_tab_stops():
    source = _method_source("QueryControls", "__init__")
    assert "Gtk.FlowBox" not in source
    assert "Gtk.FlowBoxChild" not in source
    assert "filter_row.append(widget)" in source
    assert source.count("insert_filter(") >= 6
