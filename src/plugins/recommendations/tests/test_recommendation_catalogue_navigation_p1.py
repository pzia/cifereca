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


def test_old_isbn_callback_stays_revoked_after_same_book_is_reselected():
    view, _, calls, detail, row = _view((_variant(),))
    previous = next(a.callback for a in detail.actions if a.id.startswith("recommendation.search.isbn."))
    new_presentations = []
    view._master.show_detail = new_presentations.append

    view._selected_row(None)
    view._selected_row(row)
    assert view._generation == 3  # no ranking refresh between the selections
    assert previous() is False
    assert len(new_presentations) == 1
    fresh = next(
        a.callback for a in new_presentations[0].actions
        if a.id.startswith("recommendation.search.isbn.")
    )
    assert fresh() is True
    assert calls == [((ISBN_1,), {
        "field": "isbn", "online": False, "identity_id": "reader-1",
    })]


def test_same_list_key_rebound_to_another_edition_revokes_previous_isbn_action():
    view, _, calls, detail, row = _view((_variant(),))
    previous = next(a.callback for a in detail.actions if a.id.startswith("recommendation.search.isbn."))
    previous_ranked = view._ranked_by_key[row.key]
    replacement = RankedCandidate(
        item=previous_ranked.item,
        score=previous_ranked.score,
        reasons=previous_ranked.reasons,
        sources=previous_ranked.sources,
        notices=(_variant(isbn=ISBN_2, record="other-edition"),),
    )
    # Provisional and final ranking lists may reuse keys for newer catalogue
    # objects before GTK emits another selection callback.
    view._ranked_by_key[row.key] = replacement
    assert previous() is False

    new_presentations = []
    view._master.show_detail = new_presentations.append
    view._selected_row(row)
    fresh = next(
        a.callback for a in new_presentations[0].actions
        if a.id.startswith("recommendation.search.isbn.")
    )
    assert fresh() is True
    assert calls == [((ISBN_2,), {
        "field": "isbn", "online": False, "identity_id": "reader-1",
    })]


def test_same_key_callback_replaced_even_without_llm_selection_reset():
    view, _, calls, detail, row = _view((_variant(),))
    previous = next(a.callback for a in detail.actions if a.id.startswith("recommendation.search.isbn."))
    assert view._llm_selection_key == row.key
    view._selected_row(row)
    assert view._llm_selection_key == row.key
    assert previous() is False
    assert calls == []



def test_without_attested_isbn_only_explicit_local_approximate_title_search_is_offered():
    view, identity, calls, detail, row = _view((_variant(isbn=""),))
    actions = [a for a in detail.actions if a.id.startswith("recommendation.search.")]
    assert [a.id for a in actions] == ["recommendation.search.title.local"]
    assert not calls  # Rendering has no side effects.
    assert "approché" in actions[0].title
    assert actions[0].uri == ""
    assert actions[0].callback() is True
    assert calls == [(("Une édition",), {
        "field": "title", "online": False, "identity_id": "reader-1",
    })]
    identity[0] = "reader-2"
    assert actions[0].callback() is False
    assert len(calls) == 1


def test_approximate_title_search_rechecks_selection_and_ranking_without_network():
    view, identity, calls, detail, row = _view((_variant(isbn=""),))
    callback = next(a.callback for a in detail.actions
                    if a.id == "recommendation.search.title.local")
    view._selected_row(None)
    assert callback() is False
    view._selected_row(row)
    view._generation += 1
    assert callback() is False
    assert not calls
    view._generation -= 1
    view._selected_row(row)
    fresh = []
    view._master.show_detail = fresh.append
    view._selected_row(row)
    current = next(a.callback for a in fresh[0].actions
                   if a.id == "recommendation.search.title.local")
    view.host = NS()
    assert current() is False
    assert calls == []


def test_attested_isbn_never_becomes_fuzzy_title_query_or_auto_remote_search():
    view, identity, calls, detail, row = _view((_variant(),))
    assert len([a for a in detail.actions if a.id.startswith(
        "recommendation.search.title.")]) == 0
    assert all(a.uri == "" for a in detail.actions)
    assert calls == []
