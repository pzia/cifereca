"""P1 #1354: recommendations may only navigate to attested local ISBN searches.

Exact provider actions remain owned by the standard Search detail, never by
the recommendation ranking or a merged title.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.plugin_api import UiBookListEntry
from recommendation_engine import CatalogNoticeVariant, RankedCandidate
from recommendations import (
    UxRecommendationsView, _attested_catalogue_isbns,
)


ISBN_1 = "9780306406157"
ISBN_2 = "9780140328721"


def _variant(isbn=ISBN_1, *, provider="kobo", credential="vault-1", record="book-1"):
    return CatalogNoticeVariant(
        item=NS(title="Une édition"),
        source_provider=provider,
        source_credential=credential,
        record_id=record,
        docbase="BOOKS",
        source_collection="catalogue",
        isbn=isbn,
    )


def _view(notices, *, isbn=None):
    recorded = []
    selection = ["reader-1"]
    rendered = []
    key = "book-1"
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
    view._generation = 3
    view._provisional_active = False
    view._provider_labels = {"kobo": "Kobo", "paris": "Paris"}
    item = NS(
        title="Une édition", author="Auteur", format="EPUB",
        published_at=None, description="Résumé public", friendly_url=None,
        thumbnail_url=None, cover_url=None,
    )
    view._ranked_by_key = {
        key: RankedCandidate(
            item=item, score=80, reasons=("Sujet proche",),
            sources=("kobo",), notices=tuple(notices),
        ),
    }
    view.selected_identity_id = lambda: selection[0]
    view.host = NS(search_books=lambda *a, **kw: recorded.append((a, kw)) or True)
    view._master = NS(
        show_detail=lambda detail: rendered.append(detail),
        empty_detail=lambda *_: None,
    )
    row = UiBookListEntry(key=key, title="Une édition")
    view._selected_row(row)
    return view, selection, recorded, rendered[0], row


def test_catalogue_isbn_search_never_runs_without_click_and_is_identity_bound():
    variants = (
        _variant(), _variant(provider="kobo", credential="vault-2", record="book-2"),
        _variant(ISBN_2, provider="paris", credential="paris-1", record="rec-3"),
    )
    view, identity, calls, detail, row = _view(variants)
    assert not calls
    source_section = next(section for section in detail.sections if section.id == "source-editions")
    assert len([field for field in source_section.fields if field.id.startswith("source-edition-") and field.id != "source-edition-warning"]) == 3
    values = " ".join(str(field.value) for field in source_section.fields)
    for private in ("vault-1", "vault-2", "paris-1", "book-1", "rec-3"):
        assert private not in values
    actions = [a for a in detail.actions if a.id.startswith("recommendation.search.isbn.")]
    assert len(actions) == 2  # identical ISBN across credentials is one query
    assert all(a.callback is not None and a.uri == "" for a in actions)
    assert actions[0].callback() is True
    assert calls == [((ISBN_1,), {
        "field": "isbn", "online": False, "identity_id": "reader-1",
    })]
    identity[0] = "reader-2"
    assert actions[1].callback() is False
    assert len(calls) == 1


def test_navigation_callbacks_refuse_old_selection_or_ranking_generation():
    view, identity, calls, detail, row = _view((_variant(),))
    callback = next(a.callback for a in detail.actions if a.id.startswith("recommendation.search.isbn."))
    view._selected_row(None)
    assert callback() is False
    view._selected_row(row)
    view._generation += 1
    assert callback() is False
    assert calls == []


def test_unresolved_or_invalid_variants_never_create_search_action():
    unresolved = _variant(credential="")
    invalid = _variant("9780000000000", credential="vault-1")
    invalid_format = _variant("978BADISBNXYZ", record="bad")
    assert _attested_catalogue_isbns((unresolved, invalid_format)) == ()
    view, identity, calls, detail, row = _view((unresolved, invalid_format))
    assert not [a for a in detail.actions if a.id.startswith("recommendation.search.isbn.")]
    assert not calls
    # A coordinated source may still be described without a usable ISBN,
    # but it must never expose private coordinates or become clickable.
    source_sections = [section for section in detail.sections if section.id == "source-editions"]
    assert len(source_sections) == 1
    rendered = " ".join(str(field.value) for field in source_sections[0].fields)
    assert "édition non attestée" in rendered
    assert "vault-1" not in rendered and "bad" not in rendered
    # Pure helper receives only pre-verified CatalogNoticeVariant objects.
    assert ISBN_1 not in _attested_catalogue_isbns((_variant(isbn=""),))
    assert _attested_catalogue_isbns((invalid,)) == ()  # ISBN13 checksum


def test_local_search_unavailable_never_falls_back_to_remote_or_borrow():
    view, identity, calls, detail, row = _view((_variant(),))
    view.host = NS()
    callback = next(a.callback for a in detail.actions if a.id.startswith("recommendation.search.isbn."))
    assert callback() is False
    assert not calls
