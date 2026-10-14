"""P0 #1129: pure book-notice groups, no implicit file/cover rescans."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.epub_metadata import notice_rows as core_notice_rows
from cifereca.gui.book_notice_controller import (
    add_book_notice,
    project_book_notice_groups,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "book_notice_controller.py"


def _fake_detail():
    events = []
    detail = NS(
        _show_description=lambda summary: events.append(("description", summary)),
        add_group=lambda title, rows: events.append(("group", title, rows)),
    )
    return detail, events


def test_controller_without_gtk_and_stable_mainwindow_facade():
    source = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(source)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(x == "import gi" or x.startswith("from gi ")
                   or "main_window" in x for x in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    window = next(node for node in main.body
                  if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in window.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_add_book_notice")
    assert [arg.arg for arg in method.args.args] == ["self", "detail", "item"]
    assert [arg.arg for arg in method.args.kwonlyargs] == ["include_description"]
    assert [value.value for value in method.args.kw_defaults] == [True]
    source = ast.unparse(method)
    assert "book_notice_controller.add_book_notice" in source
    assert "notice_rows=notice_rows" in source
    assert "include_description=include_description" in source


def test_empty_metadata_does_not_render_empty_groups_or_infer_cover():
    detail, events = _fake_detail()
    window = NS(_book_description=lambda item: None)
    calls = Mock(return_value=[])
    add_book_notice(window, detail, {}, notice_rows=calls)
    assert events == [("description", None)]
    calls.assert_called_once_with({})


def test_summary_precedes_notice_and_can_be_skipped_when_already_rendered():
    item = {"metadata": {"epub_metadata": {"subjects": ["Roman"]}}}
    detail, events = _fake_detail()
    window = NS(_book_description=lambda _: "Description locale")
    add_book_notice(window, detail, item, notice_rows=core_notice_rows)
    assert events == [
        ("description", "Description locale"),
        ("group", "Notice EPUB", [("Sujets", "Roman")]),
    ]
    events.clear()
    window._book_description = lambda _: (_ for _ in ()).throw(
        AssertionError("summary already rendered; must not fetch it again")
    )
    add_book_notice(
        window, detail, item, notice_rows=core_notice_rows,
        include_description=False,
    )
    assert events == [("group", "Notice EPUB", [("Sujets", "Roman")])]


def test_core_epub_projection_removes_essential_fields_but_preserves_notice():
    metadata = {
        "epub_metadata": {
            "languages": ["fr"], "publishers": ["Maison EPUB"],
            "subjects": ["Fiction"], "dates": ["2025"],
            "contributors": [{"kind": "contributor", "name": "Alice", "role": "ill"}],
        },
        "catalogue_metadata": {"languages": ["en"], "publisher": "Maison catalogue"},
    }
    groups = project_book_notice_groups({"metadata": metadata}, notice_rows=core_notice_rows)
    assert [title for title, _ in groups] == ["Notice EPUB", "Catalogue"]
    local_rows, catalogue_rows = groups[0][1], groups[1][1]
    assert ("Éditeur", "Maison EPUB") in local_rows
    assert ("Sujets", "Fiction") in local_rows
    assert ("Contributeurs", "Alice · ill") in local_rows
    assert ("Éditeur", "Maison catalogue") in catalogue_rows
    for rows in (local_rows, catalogue_rows):
        assert all(field not in {
            "Source", "Provenance", "Format", "Langue", "Langues", "Disponibilité",
        } for field, _ in rows)


def test_no_duplicate_catalogue_section_when_it_only_repeats_language():
    calls = []
    def project(meta):
        calls.append(meta)
        if meta.get("catalogue"):
            return [("Langues", "en")]
        return [("Éditeur", "Original"), ("Format", "EPUB")]
    data = {"metadata": {"catalogue_metadata": {"catalogue": True}}}
    groups = project_book_notice_groups(data, notice_rows=project)
    assert groups == [("Notice", [("Éditeur", "Original")])]
    assert len(calls) == 2
    assert calls[0] is data["metadata"]
    assert calls[1] is data["metadata"]["catalogue_metadata"]


def test_cover_schema_reports_known_status_and_detection_provenance():
    item = {"metadata": {
        "epub_metadata_schema": 2,
        "epub_cover_status": "extracted",
        "epub_cover": {"method": "first_spine"},
    }}
    groups = project_book_notice_groups(item, notice_rows=lambda _: [])
    assert groups == [("Image du livre", [
        ("Couverture", "Extraite du fichier"),
        ("Détection", "Image unique de la première page (déduction)"),
    ])]


def test_cover_missing_unreadable_or_unknown_are_not_invented():
    known = [
        ("no_usable_image", "Aucune image exploitable"),
        ("metadata_unreadable", "Métadonnées illisibles"),
        ("unknown_new_status", "À réinspecter"),
        (None, "À réinspecter"),
    ]
    for status, label in known:
        metadata = {"epub_metadata_schema": 2, "epub_cover_status": status}
        assert project_book_notice_groups(
            {"metadata": metadata}, notice_rows=lambda _: []
        ) == [("Image du livre", [("Couverture", label)])]
    assert project_book_notice_groups(
        {"metadata": {"epub_cover_status": "extracted"}},
        notice_rows=lambda _: [],
    ) == []


def test_declared_guide_and_unknown_methods_preserve_exact_attribution():
    for method, expected in [
        ("declared", "Déclaration EPUB"),
        ("declared_page", "Page de couverture déclarée"),
        ("guide", "Guide de couverture"),
        ("future-method", "future-method"),
    ]:
        item = {"metadata": {"epub_metadata_schema": 2,
                             "epub_cover": {"method": method}}}
        groups = project_book_notice_groups(item, notice_rows=lambda _: [])
        assert groups == [("Image du livre", [
            ("Couverture", "À réinspecter"), ("Détection", expected),
        ])]


def test_deterministic_order_notice_catalogue_then_cover_and_no_extra_scan():
    invocations = []
    def project(meta):
        invocations.append(meta)
        return [("Publication", str(meta["published_at"]))]
    metadata = {
        "published_at": "2025",
        "catalogue_metadata": {"published_at": "2024"},
        "epub_metadata_schema": 2,
    }
    groups = project_book_notice_groups({"metadata": metadata}, notice_rows=project)
    assert [title for title, _ in groups] == [
        "Notice", "Catalogue", "Image du livre",
    ]
    assert len(invocations) == 2
    assert invocations[0] is metadata
    assert invocations[1] is metadata["catalogue_metadata"]


def test_closed_window_does_not_access_metadata_projection_or_detail():
    detail, events = _fake_detail()
    window = NS(_closed=True)
    item = {"metadata": {"epub_metadata": {"subjects": ["Private"]}}}
    add_book_notice(
        window, detail, item,
        notice_rows=lambda _: (_ for _ in ()).throw(
            AssertionError("closed window must not render notice")
        ),
    )
    assert events == []
