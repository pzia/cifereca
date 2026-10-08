from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/query_controls.py"


def _query_controls_init() -> str:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "QueryControls")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    return ast.unparse(method)


def test_all_five_filters_share_one_horizontal_row():
    source = _query_controls_init()
    assert "filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)" in source
    assert source.count("= insert_filter(") == 5
    assert "Gtk.FlowBox" not in source


def test_narrow_width_uses_horizontal_scroll_instead_of_wrapping():
    source = _query_controls_init()
    assert "filter_scroller = Gtk.ScrolledWindow()" in source
    assert "filter_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)" in source
    assert "filter_scroller.set_propagate_natural_height(True)" in source


def test_primary_search_row_remains_separate_and_horizontally_aligned():
    source = _query_controls_init()
    assert "self.primary = Gtk.Box(spacing=8)" in source
    assert "self.primary.append(self.entry)" in source
    assert source.index("self.primary.append(self.entry)") < source.index("filter_scroller = Gtk.ScrolledWindow()")
