"""#1599: Youth public detail search actions are bound to the selected edition.

Synthetic public host, no GTK, network, library mutation, implicit download or
title-based proof of holdings. The real host keeps search/availability rights.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from youth_page import YouthView
from tests.test_youth_page import youth_book


def _fixture():
    first = youth_book(
        record_id="first", title="Same work",
        identifiers={"isbn": "9780306406157", "source_item_id": "first"},
    )
    second = youth_book(
        record_id="second", title="Same work",
        identifiers={"isbn": "9780140328721", "source_item_id": "second"},
    )
    calls, shown, emptied, toasts = [], [], [], []
    identity = ["alice"]
    view = object.__new__(YouthView)
    view.rows = {"first": first, "second": second}
    view.selected_book = None
    view.master = NS(
        show_detail=shown.append, empty_detail=lambda: emptied.append(True),
    )
    view.host = NS(
        search_books=lambda *args, **kwargs: calls.append((args, kwargs)) or True,
        toast=toasts.append,
    )
    view.identity_id = lambda: identity[0]
    return view, identity, calls, shown, emptied, toasts


def test_old_detail_actions_never_search_after_identity_change():
    view, identity, calls, shown, _, _ = _fixture()
    YouthView.selected(view, NS(key="first"))
    title_action, isbn_action = shown[-1].actions
    identity[0] = "bob"
    assert title_action.callback() is False
    assert isbn_action.callback() is False
    assert calls == []
    # Returning to the original verified identity without selecting another
    # edition still allows the original explicit local lookup.
    identity[0] = "alice"
    assert title_action.callback() is True
    assert calls == [(("Same work",), {
        "field": "title", "online": False, "identity_id": "alice",
    })]


def test_homonymous_editions_do_not_reactivate_old_actions():
    view, _, calls, shown, emptied, _ = _fixture()
    YouthView.selected(view, NS(key="first"))
    old_title, old_isbn = shown[-1].actions
    YouthView.selected(view, NS(key="second"))
    new_title, new_isbn = shown[-1].actions
    assert old_title.callback() is False
    assert old_isbn.callback() is False
    assert new_isbn.callback() is True
    assert calls == [(("9780140328721",), {
        "field": "isbn", "online": False, "identity_id": "alice",
    })]
    # A second visit to the first edition cannot revive actions from the
    # first visit, even if both book objects and keys are unchanged.
    YouthView.selected(view, NS(key="first"))
    assert new_title.callback() is False
    assert old_title.callback() is False
    assert shown[-1].actions[0].callback() is True
    YouthView.selected(view, None)
    assert shown[-1].actions[0].callback() is False
    assert emptied == [True]
    assert len(calls) == 2


def test_cache_replacement_invalidates_old_selected_action_even_without_callback():
    view, _, calls, shown, _, _ = _fixture()
    YouthView.selected(view, NS(key="first"))
    stale_action = shown[-1].actions[0]
    view.rows = {"first": youth_book(record_id="first", title="Different book")}
    assert stale_action.callback() is False
    assert calls == []


def test_unknown_or_empty_selection_keeps_no_action_or_implicit_query():
    view, _, calls, shown, emptied, _ = _fixture()
    YouthView.selected(view, NS(key="absent"))
    assert view.selected_book is None
    assert not shown and emptied == [True]
    YouthView.selected(view, None)
    assert emptied == [True, True]
    assert calls == []


def test_open_selected_rejects_evicted_book_and_disposed_page():
    view, _, calls, shown, _, toasts = _fixture()
    YouthView.selected(view, NS(key="first"))
    # The backing catalogue changed without a new GTK selection callback.
    view.rows = {"first": youth_book(record_id="first", title="Other edition")}
    YouthView.open_selected(view)
    assert toasts == ["Sélection périmée : choisissez à nouveau un livre Jeunesse."]
    assert calls == []

    view.disposed = True
    YouthView.open_selected(view)
    assert len(toasts) == 1
