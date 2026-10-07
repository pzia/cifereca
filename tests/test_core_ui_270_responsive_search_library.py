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


def test_master_detail_preserves_detail_width_at_minimum_window_width():
    source = _method_source("MasterDetailPage", "__init__")

    assert "notify::max-position" in source
    assert "adapt_paned_position" in source
    assert "max-position" in source
    assert "paned.set_position(min(440, max(0, max_position - 240)))" in source


def test_main_view_stack_does_not_inherit_hidden_page_width():
    source = _method_source("MainWindow", "_build_main_shell")

    assert "self.view_stack.set_hhomogeneous(False)" in source
