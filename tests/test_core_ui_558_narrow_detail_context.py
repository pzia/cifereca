from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"


def _class_method_source(class_name: str, method_name: str) -> str:
    tree = ast.parse(MAIN_WINDOW.read_text(encoding="utf-8"))
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )
    return ast.unparse(method)


def test_shell_exposes_sidebar_and_can_release_it_for_narrow_detail():
    build = _class_method_source("MainWindow", "_build_main_shell")
    setter = _class_method_source("MainWindow", "_set_narrow_detail_shell")

    assert "self.sidebar = sidebar" in build
    assert "sidebar.set_visible(not bool(active))" in setter


def test_narrow_detail_hides_list_only_chrome_and_restores_it():
    init = _class_method_source("MasterDetailPage", "__init__")
    chrome = _class_method_source("MasterDetailPage", "_set_narrow_detail_chrome")
    show_detail = _class_method_source("MasterDetailPage", "_show_narrow_detail")
    show_list = _class_method_source("MasterDetailPage", "_show_narrow_list")

    assert "self.page_header = header" in init
    assert "self.page_header.set_visible(not active)" in chrome
    assert "self.toolbar.set_visible(not active)" in chrome
    assert "self.detail_back.set_visible(active)" in chrome
    assert 'getattr(self.window, "_set_narrow_detail_shell", None)' in chrome

    assert "self._set_narrow_detail_chrome(True)" in show_detail
    assert "self.paned.set_position(0)" in show_detail
    assert "self._set_narrow_detail_chrome(False)" in show_list


def test_narrow_detail_transition_does_not_mutate_query_state_or_selection():
    show_detail = _class_method_source("MasterDetailPage", "_show_narrow_detail")
    show_list = _class_method_source("MasterDetailPage", "_show_narrow_list")

    combined = show_detail + "\n" + show_list
    for forbidden in (
        "controls.set_state",
        "selection.set_selected",
        "clear_rows",
        "run_search",
        "refresh_library",
    ):
        assert forbidden not in combined


def test_unmapping_page_restores_shell_navigation():
    init = _class_method_source("MasterDetailPage", "__init__")

    assert '"unmap"' in init
    assert "_set_narrow_detail_shell" in init
