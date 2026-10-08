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


def test_master_detail_switches_to_single_pane_below_breakpoint():
    source = _method_source("MasterDetailPage", "__init__")

    assert "notify::max-position" in source
    assert "adapt_paned_position" in source
    assert "self._narrow_breakpoint = MASTER_DETAIL_NARROW_WINDOW_WIDTH" in source
    assert "window_width = int(self.window.get_width())" in source
    assert "master_detail_is_narrow(" in source
    assert "narrow = max_position < self._narrow_breakpoint" not in source
    assert "paned.set_position(0 if self._narrow_detail_visible else max_position)" in source
    assert "paned.set_position(min(440, max(0, max_position - 360)))" in source
    assert "Retour à la liste" in source


def test_main_view_stack_does_not_inherit_hidden_page_width():
    source = _method_source("MainWindow", "_build_main_shell")

    assert "self.view_stack.set_hhomogeneous(False)" in source

def test_narrow_selection_opens_full_width_detail_and_back_returns_to_list():
    selected = _method_source("MasterDetailPage", "_selection_changed")
    detail = _method_source("MasterDetailPage", "_show_narrow_detail")
    back = _method_source("MasterDetailPage", "_show_narrow_list")

    assert "self._show_narrow_detail()" in selected
    assert "self._set_narrow_detail_context(True)" in detail
    assert "self.paned.set_position(0)" in detail
    assert "self.detail_back.set_visible(True)" in detail
    assert "self._set_narrow_detail_context(False)" in back
    assert "self.paned.set_position(max_position)" in back
    assert "self.detail_back.set_visible(False)" in back


def test_narrow_detail_hides_list_context_without_destroying_state():
    source = _method_source("MasterDetailPage", "_set_narrow_detail_context")

    assert "self.page_header.set_visible(not active)" in source
    assert "self.toolbar.set_visible(not active)" in source
    assert "sidebar = getattr(self.window, 'sidebar', None)" in source
    assert "sidebar.set_visible(False)" in source
    assert "sidebar.set_visible(bool(restore))" in source


def test_main_shell_exposes_sidebar_for_contextual_narrow_detail():
    source = _method_source("MainWindow", "_build_main_shell")

    assert "self.sidebar = sidebar" in source
