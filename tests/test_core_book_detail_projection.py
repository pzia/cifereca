from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(class_name: str, method_name: str) -> ast.FunctionDef:
    if class_name == "MainWindow" and method_name == "_library_selected":
        owner = ast.parse(MAIN.with_name("library_detail_controller.py").read_text(encoding="utf-8"))
        return next(node for node in owner.body
                    if isinstance(node, ast.FunctionDef) and node.name == "library_selected")
    tree = ast.parse(gui_source_text(MAIN))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)


def _calls(method: ast.FunctionDef, attr: str) -> list[ast.Call]:
    return [
        node for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]


def _uses_projection_builder(call: ast.Call) -> bool:
    return bool(
        call.args
        and isinstance(call.args[0], ast.Call)
        and isinstance(call.args[0].func, ast.Name)
        and call.args[0].func.id == "book_detail_presentation"
    )


def test_detail_panel_summary_delegates_to_shared_projection():
    method = _method("DetailPanel", "show_summary")
    calls = _calls(method, "show_presentation")
    assert len(calls) == 1
    assert _uses_projection_builder(calls[0])


def test_remote_search_details_use_shared_projection():
    for method_name in ("show_search", "show_item_detail"):
        method = _method("DetailPanel", method_name)
        calls = _calls(method, "show_presentation")
        assert calls, method_name
        assert any(_uses_projection_builder(call) for call in calls), method_name


def test_core_search_and_library_use_same_projection_before_render():
    for method_name in ("_search_selected", "_library_selected"):
        method = _method("MainWindow", method_name)
        calls = _calls(method, "show_presentation")
        assert calls, method_name
        assert any(_uses_projection_builder(call) for call in calls), method_name


def test_plugin_master_detail_uses_same_detail_panel_renderer():
    method = _method("PublicBookMasterDetailView", "show_detail")
    calls = _calls(method, "show_presentation")
    assert len(calls) == 1
    call = calls[0]
    assert call.args
    assert isinstance(call.args[0], ast.Name)
    assert call.args[0].id == "detail"
