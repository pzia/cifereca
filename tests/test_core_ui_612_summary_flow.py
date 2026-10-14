from __future__ import annotations

from tests.gui_source import gui_source_text

import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(name: str) -> str:
    if name == "_add_book_notice":
        owner = ast.parse(SOURCE.with_name("book_notice_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(node for node in owner.body
                                if isinstance(node, ast.FunctionDef) and node.name == "add_book_notice"))
    if name == "_library_selected":
        owner = ast.parse(SOURCE.with_name("library_detail_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(node for node in owner.body
                                if isinstance(node, ast.FunctionDef) and node.name == "library_selected"))
    tree = ast.parse(gui_source_text(SOURCE))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_local_book_description_prefers_epub_summary():
    source = _method("_book_description")
    assert "metadata.get('epub_metadata')" in source
    assert ".get('description')" in source


def test_search_local_summary_is_rendered_in_primary_presentation():
    source = _method("_search_selected")
    assert "description = self._book_description(item)" in source
    assert "description=description" in source
    assert "self._add_book_notice(self.search_page.detail, item, include_description=False)" in source


def test_library_summary_is_rendered_before_library_notice_extensions():
    source = _method("_library_selected")
    assert "description = window._book_description(item)" in source
    assert "description=description" in source
    presentation = source.index("detail.show_presentation")
    presence = source.index("detail.add_group('Présence / Bibliothèques / Sources'")
    notice = source.index("window._add_book_notice(detail, item, include_description=False)")
    assert presentation < presence < notice


def test_notice_can_skip_summary_to_avoid_duplicate_render():
    source = _method("_add_book_notice")
    assert "include_description=True" in source
    assert "if include_description:" in source
    assert "detail._show_description(window._book_description(item))" in source
