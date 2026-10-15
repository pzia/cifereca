"""#1599: Recommendations uses the public detail model safely and by identity.

No GTK, backend, acquisition or library access. Selected book actions stay
scoped to the active catalogue row and user identity.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import UiBookListEntry
from recommendation_engine import RankedCandidate
from recommendations import UxRecommendationsView, _public_cover_url


@pytest.mark.parametrize("unsafe", (
    "file:///home/PRIVATE_USER/cover.jpg",
    "javascript:PRIVATE_TOKEN",
    "https://user:PRIVATE_SECRET@example.org/cover.jpg",
    "https://example.org/cover.jpg?token=PRIVATE_SIGNED",
    "https://example.org/cover.jpg#PRIVATE_ID",
    "http://localhost/PRIVATE_COVER",
    "http://127.0.0.1/PRIVATE_COVER",
    "http://169.254.169.254/latest",
    "https://reader.local/cover.jpg",
    "http://printer.lan/PRIVATE_IMAGE",
    "https://host.home.arpa/PRIVATE_COVER",
    "https://example.org:8443/cover.jpg",
    "https://example.org/cover.jpg\\evil",
    "https://example.org/cover.jpg\x01PRIVATE_TOKEN",
    "https://example.org/" + "x" * 2100,
))
def test_unsafe_cover_is_never_passed_to_public_ui(unsafe):
    assert _public_cover_url({"thumbnail_url": unsafe}) is None
    assert _public_cover_url({
        "thumbnail_url": unsafe, "cover_url": "https://example.org/books/safe.jpg",
    }) == "https://example.org/books/safe.jpg"


def _make_view(item):
    view = object.__new__(UxRecommendationsView)
    view._llm_generation = 0
    view._llm_selection_key = None
    view._llm_handle = None
    view._llm_selected_row = None
    view._llm_explanation = None
    view._llm_enabled = False
    view._llm_button = None
    view._llm_hint = None
    view._busy = False
    view._generation = 1
    view._provisional_active = False
    view._provider_labels = {"kobo": "Kobo"}
    view._ranked = [RankedCandidate(
        item=item, score=92, reasons=("Auteur commun",),
        sources=("kobo",),
    )]
    view._matches = lambda _ranked: True
    view._candidate_key = lambda _ranked: "ed1"
    view.selected_identity_id = lambda: "reader-1"
    rows, details, empties = [], [], []
    view._master = NS(
        show_rows=lambda entries, **kwargs: rows.extend(entries),
        show_detail=details.append,
        empty_detail=lambda *args: empties.append(args),
    )
    view.host = NS(search_books=lambda *a, **kw: True)
    return view, rows, details, empties


def test_list_and_detail_share_safe_cover_and_preserve_multiple_authors():
    item = {
        "title": "One work", "authors": ["Alice", "Bob", "Alice"],
        "format": "EPUB",
        "friendly_url": "https://example.org/notice/ed1",
        "thumbnail_url": "https://example.org/signed?token=PRIVATE_SECRET",
        "cover_url": "https://example.org/safe-cover.jpg",
    }
    view, rows, details, empty = _make_view(item)
    view._render(preserve_summary=True)
    assert len(rows) == 1
    assert rows[0].thumbnail_url == "https://example.org/safe-cover.jpg"
    view._selected_row(rows[0])
    assert len(details) == 1
    shown = details[0]
    assert shown.title == "One work"
    assert shown.subtitle == "Alice, Bob"
    assert shown.thumbnail_url == rows[0].thumbnail_url
    catalogue = next(s for s in shown.sections if s.id == "catalogue")
    author_fields = [f for f in catalogue.fields if f.id.startswith("author-")]
    assert [(f.label, f.value, f.kind) for f in author_fields] == [
        ("Auteur", "Alice", "author"), ("Auteur", "Bob", "author"),
    ]
    assert all(a.id != "purchase" for a in shown.actions)
    assert empty == []


def test_stale_selection_clears_old_book_and_invalidates_action():
    item = {
        "title": "One work", "author": "Alice",
        "thumbnail_url": "https://example.org/cover.jpg",
    }
    view, rows, details, empties = _make_view(item)
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    assert len(details) == 1
    assert not empties
    # The async GTK selection callback can arrive after a filter or ranking
    # update that removed this key: no old detail, rights or actions remain.
    view._ranked_by_key.clear()
    view._selected_row(rows[0])
    assert len(details) == 1
    assert len(empties) == 1
    assert empties[0][0] == "Notice indisponible"
    assert view._llm_selection_key is None
    assert view._llm_selected_row is None


def test_no_results_and_clear_selection_display_explicit_empty_detail():
    view, rows, details, empties = _make_view({
        "title": "One work", "authors": ["Alice"],
    })
    view._ranked = []
    view._render(preserve_summary=True)
    assert not rows and not details
    assert empties[0][0] == "Aucune recommandation sélectionnée"
    view._selected_row(UiBookListEntry(key="absent", title="Old title"))
    assert empties[-1][0] == "Notice indisponible"
    view._selected_row(None)
    assert empties[-1][0] == "Aucune recommandation sélectionnée"
    assert not details


def test_independent_provenance_sources_and_format_use_public_field_kinds():
    item = {
        "title": "Un même ouvrage", "authors": ["Alice", "Bob"],
        "format": "EPUB", "cover_url": "https://example.org/cover.jpg",
    }
    view, rows, details, _ = _make_view(item)
    view._provider_labels = {"kobo": "Kobo", "bnf": "BnF"}
    view._ranked = [RankedCandidate(
        item=item, score=92, reasons=("Auteur commun",),
        sources=("kobo", "bnf", "kobo"),
    )]
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    presentation = details[0]
    catalogue = next(section for section in presentation.sections if section.id == "catalogue")
    sources = [field for field in catalogue.fields if field.label == "Source"]
    assert [(field.id, field.value, field.kind) for field in sources] == [
        ("source-0", "Kobo", "copy"),
        ("source-1", "BnF", "copy"),
    ]
    formats = [field for field in catalogue.fields if field.id == "format"]
    assert len(formats) == 1
    assert (formats[0].value, formats[0].kind) == ("EPUB", "all")
    assert presentation.actions == ()  # source labels never confer rights


def test_attested_languages_are_projected_individually_with_public_field_kind():
    item = {
        "title": "Livre multilingue",
        "authors": ["Alice", "Bob"],
        "language": "fr",
        "languages": ["en", "fr"],
        "metadata": {"languages": ["de", "en"]},
    }
    view, rows, details, _ = _make_view(item)
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    catalogue = next(section for section in details[0].sections if section.id == "catalogue")
    languages = [field for field in catalogue.fields if field.label == "Langue"]
    assert [(field.id, field.value, field.kind) for field in languages] == [
        ("language-0", "fr", "language"),
        ("language-1", "en", "language"),
        ("language-2", "de", "language"),
    ]
    assert details[0].actions == ()


def test_no_language_metadata_never_creates_synthetic_language_field():
    view, rows, details, _ = _make_view({
        "title": "Titre français sans langue attestée",
        "authors": ["Auteur français"],
        "format": "EPUB",
    })
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    catalogue = next(section for section in details[0].sections if section.id == "catalogue")
    assert all(field.label != "Langue" for field in catalogue.fields)
