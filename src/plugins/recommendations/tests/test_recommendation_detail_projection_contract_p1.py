"""#1599: Recommendations uses the public detail model safely and by identity.

No GTK, backend, acquisition or library access. Selected book actions stay
scoped to the active catalogue row and user identity.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import UiBookListEntry
from recommendation_engine import RankedCandidate
from recommendations import UxRecommendationsView, _public_cover_url, _public_recommendation_notice, _public_candidate_authors, _reference_label, _public_candidate_description


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
    assert rows[0].subtitle == "Alice, Bob"
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


@pytest.mark.parametrize("candidate_keys", (
    ("edition", "edition", "edition:1", "edition:2"),
    ("edition", "edition:2", "edition", "edition:3"),
    ("recommendation-0", "", "recommendation-1", "recommendation-1"),
))
def test_colliding_recommendation_keys_never_select_another_book(candidate_keys):
    """#1599: synthetic suffixes cannot overwrite existing or future keys."""
    view, rows, details, empties = _make_view({"title": "seed"})
    view._ranked = [
        RankedCandidate(
            item={"title": f"Edition {index}", "key": key, "authors": ["Author"]},
            score=80 + index, reasons=("Source attestée",), sources=("kobo",),
        )
        for index, key in enumerate(candidate_keys)
    ]
    view._candidate_key = lambda ranked: ranked.item["key"]
    view._render(preserve_summary=True)

    visible_keys = [row.key for row in rows]
    assert len(rows) == len(candidate_keys)
    assert len(visible_keys) == len(set(visible_keys))
    assert len(view._ranked_by_key) == len(rows)
    assert all(
        view._ranked_by_key[row.key].item["title"] == row.title
        for row in rows
    )
    # A raw future key keeps its own row; generated keys are always unique.
    for index, original in enumerate(candidate_keys):
        if original:
            assert view._ranked_by_key.get(original) is not None

    for row in rows:
        view._selected_row(row)
    assert [detail.title for detail in details] == [
        f"Edition {index}" for index in range(len(candidate_keys))
    ]
    assert empties == []
    assert all(detail.actions == () for detail in details)


def test_same_candidate_key_collision_keeps_original_key_stable():
    view, rows, details, _ = _make_view({"title": "seed"})
    view._ranked = [
        RankedCandidate(
            item={"title": title, "key": "same", "authors": []},
            score=80, reasons=(), sources=(),
        )
        for title in ("First", "Second", "Third")
    ]
    view._candidate_key = lambda ranked: ranked.item["key"]
    view._render(preserve_summary=True)
    assert [row.key for row in rows] == ["same", "same:1", "same:2"]
    for row in rows:
        view._selected_row(row)
    assert [detail.title for detail in details] == ["First", "Second", "Third"]


class PoisonPublicMetadata:
    def __str__(self):
        raise AssertionError("PRIVATE_PROVIDER_TOKEN /home/PRIVATE_PROFILE")


def test_malformed_provider_scalar_values_never_escape_to_public_detail():
    item = {
        "title": {"PRIVATE_PROVIDER_ACCOUNT": "not a book title"},
        "authors": ["Alice"],
        "format": PoisonPublicMetadata(),
        "published_at": "2026\nPRIVATE_SESSION",
        "description": "Summary\u202ePRIVATE_BIDI",
        "summary": "Résumé éditeur attesté",
    }
    view, rows, details, _ = _make_view(item)
    view._render(preserve_summary=True)
    assert len(rows) == 1
    assert rows[0].title == "Sans titre"
    view._selected_row(rows[0])
    presentation = details[0]
    assert presentation.title == rows[0].title
    fields = [field for section in presentation.sections for field in section.fields]
    assert not any(field.id in {"format", "published"} for field in fields)
    assert [(field.id, field.value) for field in fields if field.id == "description"] == [
        ("description", "Résumé éditeur attesté"),
    ]
    assert "PRIVATE" not in repr((rows, presentation.title, fields))
    assert _public_candidate_description(item) == "Résumé éditeur attesté"


def test_recommendation_title_and_reference_selector_share_safe_public_fallback():
    item = {"title": PoisonPublicMetadata(), "authors": [], "identifiers": {}}
    view, rows, details, _ = _make_view(item)
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    assert rows[0].title == details[0].title == _reference_label(item) == "Sans titre"


def test_recommendation_scalar_text_rejects_untrusted_string_subclasses():
    class SyntheticSecret(str):
        pass

    item = {
        "title": SyntheticSecret("PRIVATE_ACCOUNT"),
        "description": SyntheticSecret("PRIVATE_TOKEN"),
        "summary": "Notice publique",
    }
    view, rows, details, _ = _make_view(item)
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    assert rows[0].title == details[0].title == "Sans titre"
    fields = [field for section in details[0].sections for field in section.fields]
    assert next(field.value for field in fields if field.id == "description") == "Notice publique"


def test_recommendations_authors_reject_private_untrusted_cache_objects():
    class Poison:
        def __str__(self):
            raise AssertionError("PRIVATE_ACCOUNT_TOKEN")

    class SecretString(str):
        pass

    item = {
        "title": "Une édition attestée",
        "author": Poison(),
        "authors": [
            " Alice ", Poison(), "PRIVATE\nSESSION",
            SecretString("PRIVATE_CREDENTIAL"),
            {"value": Poison(), "label": " Bob "},
            {"PRIVATE_PROVIDER_KEY": "not a public author"},
            "Alice",
        ],
        "metadata": {
            "creators": ["Claire", {"name": "David"}],
            "epub_metadata": {"author": "Émile"},
        },
    }
    view, rows, details, _ = _make_view(item)
    view._render(preserve_summary=True)
    view._selected_row(rows[0])

    expected = ("Émile", "Alice", "Bob", "Claire", "David")
    # Preserve field order (author before authors), then source order for each field.
    assert _public_candidate_authors(item) == expected
    assert rows[0].subtitle == details[0].subtitle == ", ".join(expected)
    catalogue = next(section for section in details[0].sections if section.id == "catalogue")
    assert [field.value for field in catalogue.fields if field.kind == "author"] == list(expected)
    assert "PRIVATE" not in repr((rows[0].subtitle, details[0].subtitle))


def test_recommendation_author_projection_is_bounded_and_reference_consistent():
    item = {
        "title": "Une édition",
        "authors": [f"Author {i}" for i in range(200)],
    }
    authors = _public_candidate_authors(item)
    assert authors == tuple(f"Author {i}" for i in range(32))
    view, rows, details, _ = _make_view(item)
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    assert rows[0].subtitle == details[0].subtitle == ", ".join(authors)
    assert _reference_label(item) == "Une édition — Author 0"


def test_untrusted_authors_never_create_public_metadata_or_duplicate_values():
    item = {
        "title": "Un livre",
        "authors": {"PRIVATE_PASSWORD": "name in unrelated mapping"},
        "metadata": {"author": [" Alice ", "alice", "Bob"]},
    }
    view, rows, details, _ = _make_view(item)
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    assert _public_candidate_authors(item) == ("Alice", "Bob")
    assert rows[0].subtitle == details[0].subtitle == "Alice, Bob"

def test_empty_filter_invalidates_previous_detail_without_selection_callback():
    view, rows, details, empties = _make_view({"title": "Old book"})
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    assert len(details) == 1
    previous_generation = view._detail_selection_generation
    assert view._llm_selection_key == rows[0].key

    # A host may keep its old selection when the filtered list becomes empty.
    # The view must invalidate old asynchronous actions independently.
    view._matches = lambda _ranked: False
    view._render(preserve_summary=True)
    assert empties[-1][0] == "Aucune recommandation sélectionnée"
    assert view._detail_selection_generation > previous_generation
    assert view._llm_selection_key is None
    assert view._llm_selected_row is None
    assert view._ranked_by_key == {}


class SelectivelyBrokenURLRecord(dict):
    def __init__(self, *args, broken=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.broken = frozenset(broken)

    def get(self, field, default=None):
        if field in self.broken:
            raise RuntimeError("PRIVATE_SIGNED_TOKEN in untrusted accessor")
        return super().get(field, default)


@pytest.mark.parametrize("broken", (
    ("friendly_url",), ("thumbnail_url",),
    ("friendly_url", "thumbnail_url"),
))
def test_faulty_preferred_url_getters_preserve_independent_public_fallbacks(broken):
    item = SelectivelyBrokenURLRecord({
        "title": "Book attested by provider",
        "authors": ["Alice"],
        "friendly_url": "https://example.org/notice/preferred",
        "notice_url": "https://example.org/notice/fallback",
        "thumbnail_url": "https://example.org/img/preferred.jpg",
        "cover_url": "https://example.org/img/fallback.jpg",
    }, broken=broken)
    assert _public_recommendation_notice(item) == (
        "https://example.org/notice/fallback" if "friendly_url" in broken
        else "https://example.org/notice/preferred"
    )
    assert _public_cover_url(item) == (
        "https://example.org/img/fallback.jpg" if "thumbnail_url" in broken
        else "https://example.org/img/preferred.jpg"
    )
    view, rows, details, empties = _make_view(item)
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    assert rows[0].thumbnail_url == details[0].thumbnail_url
    assert details[0].actions[-1].uri == _public_recommendation_notice(item)
    assert empties == []
    assert "PRIVATE_SIGNED_TOKEN" not in repr((rows, details))


def test_two_broken_public_url_getters_do_not_disclose_exception_or_add_action():
    item = SelectivelyBrokenURLRecord({
        "title": "Book", "authors": ["Alice"],
        "friendly_url": "https://example.org/notice/preferred",
        "notice_url": "https://example.org/notice/fallback",
        "thumbnail_url": "https://example.org/img/preferred.jpg",
        "cover_url": "https://example.org/img/fallback.jpg",
    }, broken=("friendly_url", "notice_url", "thumbnail_url", "cover_url"))
    view, rows, details, _ = _make_view(item)
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    assert len(rows) == len(details) == 1
    assert rows[0].thumbnail_url is None and details[0].thumbnail_url is None
    assert details[0].actions == ()
    assert _public_recommendation_notice(item) is None
    assert _public_cover_url(item) is None
    assert "PRIVATE_SIGNED_TOKEN" not in repr((rows, details))
