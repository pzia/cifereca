"""P0 #1129 — Recent detail presentation, real controller, no GTK."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.recent_detail_controller import recent_selected


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
OWNER = MAIN.with_name("recent_detail_controller.py")


def _fake(items):
    events = []
    panel = NS(show_summary=lambda **kwargs: events.append(("summary", kwargs)))
    window = NS(
        _closed=False,
        _recent_objects=items,
        recent_page=NS(detail=panel),
        search_controls=object(),
        _add_book_notice=lambda panel, item: events.append(("notice", item)),
        _add_local_book_actions=lambda panel, item: events.append(("local", item)),
        _add_facets=lambda panel, item, controls: events.append(
            ("facets", item, controls)
        ),
    )
    stamp = lambda value, *, detailed: (
        events.append(("stamp", value, detailed)) or "Hier · 09:00"
    )
    return window, events, stamp


def test_owner_and_main_window_facade_are_explicit_and_gtk_free():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    assert not any(isinstance(n, ast.ImportFrom) and n.module in {"gi", "main_window"}
                   for n in ast.walk(tree))
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body if isinstance(n, ast.ClassDef)
               and n.name == "MainWindow")
    facade = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_recent_selected")
    assert [a.arg for a in facade.args.args] == ["self", "row"]
    assert "recent_detail_controller.recent_selected" in ast.unparse(facade)


def test_selection_renders_file_date_and_provenance_before_actions():
    item = {
        "key": "a", "title": "Titre", "author": "Autrice",
        "provider_id": "kobo", "path": "/books/Auteur/Titre.epub",
        "acquired_at": 123, "thumbnail_url": "cover",
    }
    window, events, stamp = _fake({"a": item})
    assert recent_selected(window, NS(key="a"), Path=Path, format_recent_timestamp=stamp) is None
    assert events[0] == ("stamp", 123, True)
    assert events[1][0] == "summary"
    details = events[1][1]
    assert details["title"] == "Titre"
    assert details["subtitle"] == "Autrice"
    assert details["thumbnail"] == "cover"
    assert details["badges"] == ["Kobo", "Local"]
    assert details["group_title"] == "Récupération"
    assert details["rows"] == [
        ("Fichier", "Titre.epub"),
        ("Récupéré", "Hier · 09:00"),
        ("Provenance", "Kobo"),
        ("Chemin", "/books/Auteur/Titre.epub"),
    ]
    assert [kind for kind, *_ in events[2:]] == ["notice", "local", "facets"]
    assert events[-1][2] is window.search_controls


def test_missing_path_and_missing_title_keep_neutral_placeholders():
    window, events, stamp = _fake({"missing": {"key": "missing"}})
    recent_selected(window, NS(key="missing"), Path=Path, format_recent_timestamp=stamp)
    assert events[1][1]["title"] == "Sans titre"
    assert events[1][1]["badges"] == ["Local", "Local"]
    assert events[1][1]["rows"][0] == ("Fichier", "—")
    assert events[1][1]["rows"][-1] == ("Chemin", "—")


def test_empty_selection_unknown_key_and_closed_window_are_inert():
    window, events, stamp = _fake({"a": {"key": "a"}})
    recent_selected(window, None, Path=Path, format_recent_timestamp=stamp)
    recent_selected(window, NS(key="unknown"), Path=Path, format_recent_timestamp=stamp)
    window._closed = True
    recent_selected(window, NS(key="a"), Path=Path, format_recent_timestamp=stamp)
    assert events == []


def test_close_before_page_disposal_does_not_access_deleted_fields():
    assert recent_selected(
        NS(_closed=True), NS(key="gone"), Path=Path,
        format_recent_timestamp=lambda *_a, **_kw: None,
    ) is None
