"""#1599: closing Youth revokes callbacks from the shared book detail.

Headless tests: no GTK, provider or real search.
"""
from types import SimpleNamespace as NS

from youth_catalog import YouthBook
from youth_page import YouthView, book_key


def make_view():
    book = YouthBook(
        record_id="edition-1", source_id="school", title="Two books",
        identifiers={"isbn": "9780306406157"},
    )
    shown, searches, state = [], [], []
    view = object.__new__(YouthView)
    view.disposed = False
    view.generation = 2
    view.task = NS(cancel=lambda: state.append("cancel"))
    view.unsubscribe_sources = lambda: state.append("sources")
    view.unsubscribe_identity = lambda: state.append("identities")
    view.rows = {book_key(book): book}
    view.master = NS(show_detail=shown.append, empty_detail=lambda: state.append("empty"))
    view.host = NS(search_books=lambda *args, **kwargs: searches.append((args, kwargs)) or True)
    view.identity_id = lambda: "alice"
    return view, book, shown, searches, state


def test_old_search_actions_are_revoked_when_plugin_page_closes():
    view, book, shown, searches, state = make_view()
    view.selected(NS(key=book_key(book)))
    actions = {action.id: action for action in shown[0].actions}
    assert {"search-library", "search-library-isbn"} <= set(actions)
    assert actions["search-library"].callback() is True
    assert len(searches) == 1

    before = view._detail_selection_generation
    view.dispose()
    assert view.disposed is True
    assert view.selected_book is None
    assert view._detail_selection_generation == before + 1
    assert state == ["cancel", "sources", "identities"]
    assert actions["search-library"].callback() is False
    assert actions["search-library-isbn"].callback() is False
    assert len(searches) == 1


def test_delayed_selection_after_dispose_does_not_reopen_detail():
    view, book, shown, searches, state = make_view()
    view.dispose()
    view.selected(NS(key=book_key(book)))
    view.selected(None)
    assert shown == []
    assert searches == []
    assert view.selected_book is None
    assert state == ["cancel", "sources", "identities"]


def test_fresh_view_after_dispose_keeps_explicit_search_available():
    old, book, old_shown, old_searches, _ = make_view()
    old.selected(NS(key=book_key(book)))
    previous = old_shown[0].actions[0].callback
    old.dispose()

    fresh, _, fresh_shown, fresh_searches, _ = make_view()
    fresh.selected(NS(key=book_key(book)))
    assert previous() is False
    assert fresh_shown[0].actions[0].callback() is True
    assert old_searches == []
    assert len(fresh_searches) == 1
