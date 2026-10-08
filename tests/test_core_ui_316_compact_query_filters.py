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


def test_query_filters_keep_natural_width_in_single_row():
    source = _method_source("QueryControls", "__init__")
    assert "filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)" in source
    assert "def insert_filter(widget):" in source
    assert "widget.set_hexpand(False)" in source
    assert "widget.set_halign(Gtk.Align.START)" in source
    assert "filter_row.append(widget)" in source
    assert "self.provider = insert_filter(" in source
    assert "self.types = insert_filter(" in source
    assert "self.languages = insert_filter(" in source
    assert "self.field = insert_filter(" in source
    assert "self.scope = insert_filter(" in source
