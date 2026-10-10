"""Focused, no-GTK regression checks for the shared metadata detail wiring."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


def _method(class_name, method_name):
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)


def _projection_calls(node):
    return [
        call for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "book_detail_presentation"
    ]


def test_search_and_library_pass_metadata_into_shared_projection():
    for class_name, method_name in (
        ("DetailPanel", "show_search"),
        ("DetailPanel", "show_item_detail"),
        ("MainWindow", "_search_selected"),
        ("MainWindow", "_library_selected"),
    ):
        calls = _projection_calls(_method(class_name, method_name))
        assert calls, method_name
        assert any(any(kw.arg == "metadata" for kw in call.keywords) for call in calls), method_name


def test_detail_section_uses_collapsible_rows_without_network():
    source = ast.unparse(_method("DetailPanel", "_group"))
    assert "title == 'Détail'" in source
    assert "Adw.ExpanderRow()" in source
    assert "expander.add_row(row)" in source
    assert "notify::expanded" in source
    assert "set_subtitle_lines(0)" in source
    assert "self._detail_expanded" in source
    assert "http" not in source.lower()


def test_shared_builder_keeps_essential_and_detail_sections_separate():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    builder = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "book_detail_presentation")
    assert any(arg.arg == "metadata" for arg in builder.args.kwonlyargs)
    source = ast.unparse(builder)
    assert "detail_metadata_rows(metadata)" in source
    assert "title='Détail'" in source or "title='Détail'".replace("'", '"') in source
    assert "already_shown" in source
