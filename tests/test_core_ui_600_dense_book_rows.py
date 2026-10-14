from __future__ import annotations

import ast
from pathlib import Path

from cifereca.gui.book_facets import BookFacet, compact_row_meta, project_list_facets


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
WIDGETS = ROOT / "src/cifereca/gui/book_widgets.py"
LIST_FACETS = ROOT / "src/cifereca/gui/list_facet_controller.py"


def _method(path: Path, class_name: str, method_name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def _function(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(function)


def test_dense_projection_limits_authors_and_exposes_overflow():
    facets = [
        BookFacet("author", "Auteur A", "Auteur A"),
        BookFacet("author", "Auteur B", "Auteur B"),
        BookFacet("author", "Auteur C", "Auteur C"),
        BookFacet("format", "EPUB", "epub"),
        BookFacet("format", "PDF", "pdf"),
        BookFacet("language", "Français", "fr"),
        BookFacet("collection", "Pléiade", "Pléiade"),
        BookFacet("source", "Local", "local"),
    ]
    shown = project_list_facets(facets)

    assert len(shown) <= 6
    assert [facet.label for facet in shown if facet.field == "author"] == ["Auteur A", "Auteur B"]
    assert shown[-1].field == "_overflow"
    assert shown[-1].label.startswith("+")
    assert "Auteur C" in shown[-1].tooltip


def test_dense_projection_removes_duplicate_visible_values():
    facets = [
        BookFacet("source", "Local", "local"),
        BookFacet("collection", "Local", "Local"),
        BookFacet("format", "EPUB", "epub"),
        BookFacet("format", "EPUB", "epub"),
    ]
    shown = project_list_facets(facets)
    labels = [facet.label.casefold() for facet in shown if facet.field != "_overflow"]

    assert labels.count("local") == 1
    assert labels.count("epub") == 1


def test_meta_keeps_only_information_not_already_in_facets():
    assert compact_row_meta("Local · EPUB · Disponible", ["Local", "EPUB"]) == "Disponible"
    assert compact_row_meta("Local · Local", ["Local"]) == ""
    assert compact_row_meta("Livre numérique · 2026", ["EPUB", "Kobo"]) == "Livre numérique · 2026"


def test_search_projection_includes_language_and_non_clickable_overflow():
    facade = _method(MAIN, "MainWindow", "_attach_row_facets")
    assert "list_facet_controller.attach_row_facets(" in facade
    source = _function(LIST_FACETS, "attach_row_facets")
    assert "'language': 2" in source
    assert "project_list_facets(facets)" in source
    assert "None if facet.field == '_overflow'" in source


def test_book_cell_deduplicates_meta_and_only_guards_callable_facets():
    source = _method(WIDGETS, "BookListCell", "bind")
    assert "if callable(callback)" in source
    assert "compact_row_meta(item.meta, visible_facet_labels)" in source
    assert "badge_key not in visible_keys" in source
