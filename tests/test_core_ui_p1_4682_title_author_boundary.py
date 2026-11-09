"""#4682: title/author provenance at the neutral Library -> Detail boundary.

An EPUB's optional bibliographic metadata cannot silently exchange the
canonical row title with the row author in the shared GTK presentation.
This test only qualifies the renderer; a bad persisted row remains a
separate upstream issue requiring exact source evidence.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.detail_model import book_detail_presentation
from tests.test_core_ui_1129_library_detail_controller import _context


def test_library_detail_keeps_explicit_title_and_author_with_discordant_epub_metadata():
    item = {
        "key": "artifact:synthetic-a",
        "title": "Le livre synthétique",
        "author": "Nom de l'autrice",
        "provider_id": "kobo",
        "identity_id": "alice",
        "media_format": "epub",
        "state": "local",
        "metadata": {
            "epub_metadata": {
                "title": "Autre titre déclaré",
                "authors": ["Autre autrice déclarée"],
            },
        },
    }
    window, events, projected, _lookup, call = _context(item)
    call(NS(key=item["key"]))
    assert events[1][0] == "presentation"
    assert projected[0]["title"] == item["title"]
    assert projected[0]["subtitle"] == item["author"]
    assert projected[0]["metadata"] is item["metadata"]

    presentation = book_detail_presentation(**projected[0])
    assert presentation.title == "Le livre synthétique"
    assert presentation.subtitle == "Nom de l'autrice"
    assert presentation.title != item["author"]
    assert presentation.subtitle != item["title"]


def test_inverted_persisted_row_is_not_silently_guessed_from_cover_or_filename():
    # If a caller supplies an inverted row, the UI displays it as supplied.
    # Fixing that requires tracing the source of that row, not swapping UI
    # labels or reading image/text heuristics during rendering.
    item = {
        "key": "artifact:synthetic-b",
        "title": "Nom de l'autrice",
        "author": "Le livre synthétique",
        "provider_id": "kobo",
        "identity_id": "alice",
        "media_format": "epub",
        "metadata": {"cover_url": "https://example.org/synthetic.png"},
    }
    _window, _events, projected, _lookup, call = _context(item)
    call(NS(key=item["key"]))
    assert projected[0]["title"] == item["title"]
    assert projected[0]["subtitle"] == item["author"]
