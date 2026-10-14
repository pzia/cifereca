from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"


def _method_source(class_name: str, method_name: str) -> str:
    tree = ast.parse(MAIN_WINDOW.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def test_navigation_group_header_can_shrink_at_minimum_window_width():
    owner = ROOT / "src/cifereca/gui/shell_page_controller.py"
    tree = ast.parse(owner.read_text(encoding="utf-8"))
    method = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "rebuild_plugin_pages")
    source = ast.unparse(method)

    assert "button = Gtk.ToggleButton()" in source
    assert "button_label = Gtk.Label(label=group_label, xalign=0)" in source
    assert "button_label.set_ellipsize(Pango.EllipsizeMode.END)" in source
    assert "button.set_child(button_label)" in source
