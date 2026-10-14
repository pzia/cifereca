from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(class_name: str, method_name: str):
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)


def _standalone(class_name: str, method_name: str):
    if class_name == "MainWindow" and method_name in {
        "_open_library_item", "_select_pending_library_item",
    }:
        from cifereca.gui import library_navigation
        return getattr(library_navigation, method_name.lstrip("_"))
    method = _method(class_name, method_name)
    ns = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(MAIN), "exec"), ns)
    return ns[method_name]


def test_master_detail_can_select_and_scroll_to_a_key():
    source = ast.unparse(_method("MasterDetailPage", "select_key"))
    assert "self.selection.set_selected(index)" in source
    assert "self.listview.scroll_to(index, Gtk.ListScrollFlags.FOCUS, None)" in source
    assert "return True" in source
    assert "return False" in source


def test_refresh_keeps_no_autoselection_and_narrow_detail_behavior():
    show_rows = ast.unparse(_method("MasterDetailPage", "show_rows"))
    selection = ast.unparse(_method("MasterDetailPage", "_selection_changed"))
    assert "else 0" not in show_rows
    assert "set_selected(0)" not in show_rows
    assert "_show_narrow_detail()" in selection


def test_open_library_item_navigates_without_changing_filters():
    open_item = _standalone("MainWindow", "_open_library_item")
    calls = []
    window = NS(
        navigate=lambda route: calls.append(("navigate", route)),
        _select_pending_library_item=lambda: calls.append(("select", None)),
    )
    open_item(window, "artifact:sha256:test")
    assert window._pending_library_selection_key == "artifact:sha256:test"
    assert calls == [("navigate", "library"), ("select", None)]
    assert not hasattr(window, "library_controls")


def test_pending_selection_selects_visible_book_and_clears_request():
    select = _standalone("MainWindow", "_select_pending_library_item")
    page = NS(select_key=Mock(return_value=True))
    window = NS(
        _pending_library_selection_key="artifact:sha256:test",
        library_page=page,
        _library_entries_cache=[{"key": "artifact:sha256:test"}],
        toast=Mock(),
    )
    assert select(window) is True
    page.select_key.assert_called_once_with("artifact:sha256:test")
    assert window._pending_library_selection_key is None
    window.toast.assert_not_called()


def test_pending_selection_survives_initial_library_load():
    select = _standalone("MainWindow", "_select_pending_library_item")
    window = NS(
        _pending_library_selection_key="artifact:sha256:test",
        library_page=NS(select_key=Mock(return_value=False)),
        _library_entries_cache=[],
        toast=Mock(),
    )
    assert select(window) is False
    assert window._pending_library_selection_key == "artifact:sha256:test"
    window.toast.assert_not_called()


def test_pending_selection_explains_active_filter_without_resetting_it():
    select = _standalone("MainWindow", "_select_pending_library_item")
    page = NS(select_key=Mock(return_value=False))
    window = NS(
        _pending_library_selection_key="artifact:sha256:test",
        library_page=page,
        _library_entries_cache=[{"key": "artifact:sha256:test"}],
        toast=Mock(),
        library_controls=NS(set_state=Mock()),
    )
    assert select(window) is False
    assert "masqué par les filtres actifs" in window.toast.call_args.args[0]
    window.library_controls.set_state.assert_not_called()
    assert window._pending_library_selection_key is None


def test_library_render_retries_pending_selection_after_rows_are_present():
    source = ast.unparse(_method("MainWindow", "_render_library"))
    assert source.index("library_page.show_rows") < source.index("_select_pending_library_item")


def test_detail_panel_and_master_detail_keep_distinct_class_boundaries():
    tree = ast.parse(gui_source_text(MAIN))
    classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    assert "DetailPanel" in classes
    assert "MasterDetailPage" in classes

    detail_init = next(
        node for node in classes["DetailPanel"].body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    master_init = next(
        node for node in classes["MasterDetailPage"].body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assert [arg.arg for arg in detail_init.args.args] == ["self", "window"]
    assert [arg.arg for arg in master_init.args.args] == [
        "self", "window", "title", "subtitle", "empty_title", "empty_desc"
    ]
