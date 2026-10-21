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



def test_multi_provider_exact_editions_offer_explicit_offline_source_filter():
    """Explicit provider filters do not grant a loan, account or download."""
    variants = (_variant(), _variant(ISBN_2, provider="paris",
                                     credential="paris-1", record="notice-paris"))
    view, identity, calls, detail, row = _view(variants)
    targeted = [action for action in detail.actions
                if action.id.startswith("recommendation.search.provider.")]
    assert len(targeted) == 2
    assert not calls  # Displaying a recommendation is network-inert.
    assert all(action.uri == "" for action in targeted)
    assert targeted[0].callback() is True
    assert calls == [((ISBN_1,), {
        "field": "isbn", "online": False, "identity_id": "reader-1",
        "providers": ("kobo",),
    })]
    identity[0] = "reader-2"
    assert targeted[1].callback() is False
    assert len(calls) == 1


def test_old_targeted_provider_action_cannot_escape_selection_epoch():
    variants = (_variant(), _variant(ISBN_2, provider="paris",
                                     credential="paris-1", record="notice-paris"))
    view, _identity, calls, detail, row = _view(variants)
    old = next(a.callback for a in detail.actions
               if a.id.startswith("recommendation.search.provider."))
    view._selected_row(None)
    view._selected_row(row)
    assert old() is False
    assert not calls



def test_exact_loaded_search_notice_goes_to_shared_actions_only_on_click():
    variants = (
        _variant(provider="kobo", credential="vault-1", record="book-1"),
        _variant(provider="kobo", credential="vault-2", record="book-2"),
    )
    view, identity, local_searches, _initial_detail, row = _view(variants)
    available_calls, open_calls, delivered = [], [], []

    def available(**coordinates):
        available_calls.append(coordinates.copy())
        # One specific account/edition is loaded in Search; the other isn't.
        return coordinates["record_id"] == "book-2"

    def open_exact(**coordinates):
        open_calls.append(coordinates.copy())
        return True

    view.host = NS(
        search_books=lambda *a, **kw: local_searches.append((a, kw)) or True,
        can_open_loaded_search_notice=available,
        open_loaded_search_notice=open_exact,
    )
    view._master.show_detail = delivered.append
    view._selected_row(row)
    assert len(available_calls) == 2
    assert len(open_calls) == 0  # rendering checks cache, no navigation
    actions = [a for a in delivered[-1].actions
               if a.id.startswith("recommendation.loaded-notice.")]
    assert len(actions) == 1
    assert "notice 2" in actions[0].title
    assert all(secret not in actions[0].title
               for secret in ("vault-1", "vault-2", "book-2"))
    assert actions[0].callback() is True
    assert open_calls == [{
        "provider": "kobo", "credential": "vault-2", "record_id": "book-2",
        "docbase": "BOOKS", "source_collection": "catalogue",
        "identity_id": "reader-1",
    }]
    assert local_searches == []


def test_exact_loaded_notice_refuses_stale_identity_selection_and_disposal():
    view, identity, calls, _detail, row = _view((_variant(),))
    navigations, presentations = [], []
    view.host = NS(
        can_open_loaded_search_notice=lambda **kw: True,
        open_loaded_search_notice=lambda **kw: navigations.append(kw) or True,
        search_books=lambda *a, **kw: calls.append((a, kw)) or True,
    )
    view._master.show_detail = presentations.append
    view._selected_row(row)
    action = next(a.callback for a in presentations[-1].actions
                  if a.id.startswith("recommendation.loaded-notice."))
    identity[0] = "reader-2"
    assert action() is False
    identity[0] = "reader-1"
    view._selected_row(None)
    assert action() is False
    view._selected_row(row)
    current = next(a.callback for a in presentations[-1].actions
                   if a.id.startswith("recommendation.loaded-notice."))
    view._generation += 1
    assert current() is False
    view._generation -= 1
    view._disposed = True
    assert current() is False
    assert navigations == []
    assert calls == []


def test_no_exact_route_when_host_missing_unresolved_or_fails_closed():
    unresolved = _variant(credential="")
    view, _, calls, _detail, row = _view((unresolved,))
    presentations = []
    view._master.show_detail = presentations.append
    view.host = NS(
        can_open_loaded_search_notice=lambda **kw: True,
        open_loaded_search_notice=lambda **kw: True,
    )
    view._selected_row(row)
    assert not [action for action in presentations[-1].actions
                if action.id.startswith("recommendation.loaded-notice.")]
    assert calls == []

    view._ranked_by_key[row.key] = RankedCandidate(
        item=view._ranked_by_key[row.key].item, score=75,
        reasons=("Similaire",), sources=("kobo",),
        notices=(_variant(),),
    )
    view.host = NS(
        can_open_loaded_search_notice=lambda **kw: (_ for _ in ()).throw(
            RuntimeError("PRIVATE_ACCOUNT")
        ),
        open_loaded_search_notice=lambda **kw: True,
    )
    view._selected_row(row)
    assert not [action for action in presentations[-1].actions
                if action.id.startswith("recommendation.loaded-notice.")]
    assert "PRIVATE_ACCOUNT" not in repr(presentations[-1])


def test_unresolved_first_sixteen_never_hide_attested_loaded_notice():
    """An exact identity/account-bound row can follow unresolved variants."""
    incomplete = tuple(_variant(credential="", record=f"unknown-{i}") for i in range(16))
    exact = _variant(provider="kobo", credential="vault-1", record="exact-17")
    view, identity, search_calls, _detail, row = _view((*incomplete, exact))
    availability_calls, opened, rendered = [], [], []
    coordinates = {
        "provider": "kobo", "credential": "vault-1",
        "record_id": "exact-17", "docbase": "BOOKS",
        "source_collection": "catalogue", "identity_id": "reader-1",
    }
    view.host = NS(
        search_books=lambda *a, **kw: search_calls.append((a, kw)) or True,
        can_open_loaded_search_notice=lambda **kw: (
            availability_calls.append(kw) or kw == coordinates
        ),
        open_loaded_search_notice=lambda **kw: opened.append(kw) or True,
    )
    view._master.show_detail = rendered.append
    view._selected_row(row)
    assert availability_calls == [coordinates]  # local only; no remote search
    assert not opened and not search_calls
    detail = rendered[0]
    actions = [a for a in detail.actions if a.id.startswith(
        "recommendation.loaded-notice."
    )]
    assert len(actions) == 1
    assert actions[0].callback() is True
    assert opened == [coordinates]
    identity[0] = "reader-2"
    assert actions[0].callback() is False
    assert opened == [coordinates]
    assert not search_calls


def test_attested_notice_is_capped_after_filter_not_before():
    unresolved = tuple(_variant(credential="", record=f"unknown-{i}") for i in range(32))
    verified = tuple(_variant(record=f"verified-{i}") for i in range(25))
    view, _identity, _calls, detail, _row = _view((*unresolved, *verified))
    section = next(x for x in detail.sections if x.id == "source-editions")
    editions = [f for f in section.fields if f.id.startswith("source-edition-")
                and f.id not in {"source-edition-warning", "source-edition-page", "source-edition-limit"}]
    assert len(editions) == 16
    assert all("Kobo" in f.value for f in editions)


def test_65th_verified_notice_survives_unresolved_first_64_without_network():
    incomplete = tuple(
        _variant(credential="", record=f"unknown-{index}") for index in range(64)
    )
    exact = _variant(credential="vault-2", record="valid-65")
    view, identity, searches, _detail, row = _view((*incomplete, exact))
    presented, available, opened = [], [], []
    view.host = NS(
        search_books=lambda *_args, **_kwargs: searches.append("search") or True,
        can_open_loaded_search_notice=lambda **kw: available.append(kw) or True,
        open_loaded_search_notice=lambda **kw: opened.append(kw) or True,
    )
    view._master.show_detail = presented.append
    view._selected_row(row)
    assert len(available) == 1
    assert available[0]["record_id"] == "valid-65"
    assert available[0]["credential"] == "vault-2"
    assert not searches and not opened
    actions = [a for a in presented[-1].actions
               if a.id.startswith("recommendation.loaded-notice.")]
    assert len(actions) == 1
    assert actions[0].callback() is True
    assert opened == available
    identity[0] = "reader-2"
    assert actions[0].callback() is False


def test_256th_is_bounded_and_257th_has_explicit_non_exhaustive_warning():
    unresolved = tuple(
        _variant(credential="", record=f"unresolved-{index}")
        for index in range(255)
    )
    last_visible = _variant(credential="vault-1", record="exact-256")
    hidden = _variant(credential="vault-1", record="exact-257")
    view, _, searches, _detail, row = _view((*unresolved, last_visible, hidden))
    presented, availability = [], []
    view.host = NS(
        search_books=lambda *_a, **_kw: searches.append("search") or True,
        can_open_loaded_search_notice=lambda **kw: availability.append(kw) or True,
        open_loaded_search_notice=lambda **kw: True,
    )
    view._master.show_detail = presented.append
    view._selected_row(row)
    assert [x["record_id"] for x in availability] == ["exact-256"]
    section = next(s for s in presented[-1].sections if s.id == "source-editions")
    assert any(f.id == "source-edition-limit" and "pas exhaustive" in f.value
               for f in section.fields)
    assert not searches

    # All 256 scanned candidates can be incomplete; never suggest that this
    # proves there is no exact edition in the remaining source catalogue.
    view._ranked_by_key[row.key] = RankedCandidate(
        item=view._ranked_by_key[row.key].item, score=80,
        reasons=("Sujet proche",), sources=("kobo",),
        notices=(*unresolved, _variant(credential="", record="unresolved-256"), hidden),
    )
    view._selected_row(row)
    section = next(s for s in presented[-1].sections if s.id == "source-editions")
    assert any(f.id == "source-edition-limit" for f in section.fields)
    assert not any(a.id.startswith("recommendation.loaded-notice.")
                   for a in presented[-1].actions)


def test_duplicate_provider_record_is_one_action_but_distinct_accounts_remain():
    duplicates = tuple(
        _variant(isbn=(ISBN_1 if index % 2 == 0 else ISBN_2),
                 credential="vault-1", record="same-edition")
        for index in range(32)
    )
    distinct = (
        _variant(credential="vault-2", record="same-edition"),
        _variant(credential="vault-1", record="new-edition"),
    )
    view, _, searches, _detail, row = _view((*duplicates, *distinct))
    available, opened, presented = [], [], []
    view.host = NS(
        search_books=lambda *_a, **_kw: searches.append("search") or True,
        can_open_loaded_search_notice=lambda **kw: available.append(kw) or True,
        open_loaded_search_notice=lambda **kw: opened.append(kw) or True,
    )
    view._master.show_detail = presented.append
    view._selected_row(row)
    assert len(available) == 3
    assert {(x["credential"], x["record_id"]) for x in available} == {
        ("vault-1", "same-edition"), ("vault-2", "same-edition"),
        ("vault-1", "new-edition"),
    }
    actions = [a for a in presented[-1].actions
               if a.id.startswith("recommendation.loaded-notice.")]
    assert len(actions) == 3
    assert [a.id for a in actions] == [
        "recommendation.loaded-notice.1",
        "recommendation.loaded-notice.2",
        "recommendation.loaded-notice.3",
    ]
    assert [action.callback() for action in actions] == [True] * 3
    assert len(opened) == 3
    assert not searches


def test_malformed_coordinates_do_not_create_executable_action():
    # CatalogNoticeVariant is a frozen dataclass but its values can still be
    # malformed upstream: do not let an unhashable list poison a detail view.
    malformed = _variant(credential="vault-1", record="real")
    from dataclasses import replace
    invalid = replace(malformed, source_provider=["kobo"], record_id=["fake"])
    giant = replace(malformed, source_credential="SECRET" * 100)
    view, _, searches, _detail, row = _view((invalid, giant, malformed))
    presented, available = [], []
    view.host = NS(
        search_books=lambda *_a, **_kw: searches.append("search") or True,
        can_open_loaded_search_notice=lambda **kw: available.append(kw) or True,
        open_loaded_search_notice=lambda **kw: True,
    )
    view._master.show_detail = presented.append
    view._selected_row(row)
    assert len(available) == 1
    assert available[0]["credential"] == "vault-1"
    assert "SECRET" not in repr(presented[-1])
    assert not searches


def test_exactly_sixteen_distinct_verified_notices_are_not_called_incomplete():
    variants = tuple(_variant(record=f"exact-{index}") for index in range(16))
    _view_instance, _, _calls, detail, _row = _view(variants)
    section = next(s for s in detail.sections if s.id == "source-editions")
    assert len([field for field in section.fields
                if field.id.startswith("source-edition-")
                and field.id not in {"source-edition-warning", "source-edition-page", "source-edition-limit"}]) == 16
    assert not any(field.id == "source-edition-limit" for field in section.fields)


def _page_actions(detail, prefix):
    return [action for action in detail.actions if action.id.startswith(prefix)]


def test_explicit_exact_notice_pagination_reaches_17th_and_33rd_records():
    variants = tuple(_variant(record=f"edition-{number:02}") for number in range(1, 34))
    view, identity, searches, first, row = _view(variants)
    presentations, available, selected = [], [], []
    view.host = NS(
        can_open_loaded_search_notice=lambda **kw: available.append(kw) or True,
        open_loaded_search_notice=lambda **kw: selected.append(kw) or True,
        search_books=lambda *_a, **_kw: searches.append("unexpected") or True,
    )
    view._master.show_detail = presentations.append
    view._selected_row(row)
    first_page = presentations[-1]
    first_actions = _page_actions(first_page, "recommendation.loaded-notice.")
    assert len(first_actions) == 16
    assert first_actions[0].id == "recommendation.loaded-notice.1"
    assert first_actions[-1].id == "recommendation.loaded-notice.16"
    assert [a["record_id"] for a in available] == [
        f"edition-{index:02}" for index in range(1, 17)
    ]
    assert not selected and not searches
    next_page = next(a for a in first_page.actions
                     if a.id == "recommendation.exact-notice.next")
    assert "page 2/3" in next_page.title

    assert next_page.callback() is True
    second_page = presentations[-1]
    assert next_page.callback() is False  # Old page button revoked.
    assert first_actions[0].callback() is False  # No stale source selection.
    second_actions = _page_actions(second_page, "recommendation.loaded-notice.")
    assert len(second_actions) == 16
    assert second_actions[0].id == "recommendation.loaded-notice.17"
    assert second_actions[-1].id == "recommendation.loaded-notice.32"
    assert second_actions[0].callback() is True
    assert selected[-1]["record_id"] == "edition-17"
    assert selected[-1]["credential"] == "vault-1"
    fields = next(s.fields for s in second_page.sections
                  if s.id == "source-editions")
    assert any(f.id == "source-edition-page"
               and "17–32 sur 33" in f.value for f in fields)

    forward = next(a for a in second_page.actions
                   if a.id == "recommendation.exact-notice.next")
    assert forward.callback() is True
    last_page = presentations[-1]
    last_actions = _page_actions(last_page, "recommendation.loaded-notice.")
    assert [a.id for a in last_actions] == ["recommendation.loaded-notice.33"]
    assert not _page_actions(last_page, "recommendation.exact-notice.next")
    back = next(a for a in last_page.actions
                if a.id == "recommendation.exact-notice.previous")
    assert back.callback() is True
    assert _page_actions(presentations[-1], "recommendation.loaded-notice.")[0].id == (
        "recommendation.loaded-notice.17"
    )
    assert not searches  # Only the user-clicked Core exact path was called.
    assert identity == ["reader-1"]


def test_page_changes_are_revoked_by_identity_switch_selection_and_ranking():
    variants = tuple(_variant(record=f"edition-{index}") for index in range(19))
    view, identity, searches, _first, row = _view(variants)
    presentations = []
    view._master.show_detail = presentations.append
    view._selected_row(row)
    next_page = next(a for a in presentations[-1].actions
                     if a.id == "recommendation.exact-notice.next")
    identity[0] = "reader-2"
    assert next_page.callback() is False
    identity[0] = "reader-1"

    view._selected_row(None)
    assert next_page.callback() is False
    view._selected_row(row)
    new_page = next(a for a in presentations[-1].actions
                    if a.id == "recommendation.exact-notice.next")
    assert new_page.callback() is True
    fields = next(s.fields for s in presentations[-1].sections
                  if s.id == "source-editions")
    assert any(f.id == "source-edition-page"
               and "page 2/2" in f.value for f in fields)
    old_ranked = view._ranked_by_key[row.key]
    view._ranked_by_key[row.key] = RankedCandidate(
        item=old_ranked.item, score=old_ranked.score,
        reasons=old_ranked.reasons, sources=old_ranked.sources,
        notices=(_variant(record="replacement"),),
    )
    assert new_page.callback() is False
    view._selected_row(row)
    assert not _page_actions(presentations[-1], "recommendation.exact-notice.next")
    assert len(_page_actions(presentations[-1], "recommendation.loaded-notice.")) == 0
    assert not searches


def test_same_coordinates_with_multiple_isbns_do_not_consume_two_page_slots():
    variants = [
        _variant(isbn=(ISBN_1 if index % 2 else ISBN_2),
                 credential="vault-1", record="same")
        for index in range(17)
    ]
    variants.extend(_variant(record=f"other-{i}") for i in range(15))
    view, _, searches, detail, _row = _view(tuple(variants))
    assert len(_page_actions(detail, "recommendation.loaded-notice.")) == 0
    section = next(s for s in detail.sections if s.id == "source-editions")
    assert len([f for f in section.fields if f.id.startswith("source-edition-")
                and f.id not in {"source-edition-warning",
                                  "source-edition-page",
                                  "source-edition-limit"}]) == 16
    assert not any(a.id == "recommendation.exact-notice.next"
                   for a in detail.actions)
    assert not searches


def test_pagination_never_looks_beyond_256_variants_or_invents_actions():
    invalid = tuple(_variant(credential="", record=f"missing-{i}")
                    for i in range(256))
    hidden = _variant(record="outside-cap")
    view, _, searches, detail, _row = _view((*invalid, hidden))
    section = next(s for s in detail.sections if s.id == "source-editions")
    assert any(f.id == "source-edition-limit"
               and "256 premières" in f.value for f in section.fields)
    assert not any(a.id.startswith("recommendation.exact-notice.")
                   for a in detail.actions)
    assert not searches
