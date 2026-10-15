"""#1599: a GTK profile switch revokes old Youth cached-book actions."""
from types import SimpleNamespace as NS

from youth_catalog import YouthBook
from youth_page import FilterState, YouthView, book_key


class Catalogue:
    def __init__(self, books):
        self.books = books
        self.calls = []

    def search_cached(self, identity, *args, **kwargs):
        self.calls.append(identity)
        return self.books.get(identity, ())


def build_view():
    # Both profiles expose the *same catalogue key* for unrelated notices.
    alice = YouthBook(record_id="same-id", source_id="milan", title="PRIVATE_ALICE")
    bob = YouthBook(record_id="same-id", source_id="milan", title="Bob's book")
    catalogue = Catalogue({"alice": (alice,), "bob": (bob,)})
    current = ["alice"]
    events, toasts, opens = [], [], []
    view = object.__new__(YouthView)
    view.catalog = catalogue
    view.identity_id = lambda: current[0]
    view._last_cache_identity = "alice"
    view.rows = {book_key(alice): alice}
    view.selected_book = alice
    view._detail_selection_generation = 5
    view.filter_state = lambda: FilterState()
    view.master = NS(
        empty_detail=lambda: events.append("empty"),
        show_rows=lambda rows, **opts: events.append(("rows", tuple(rows))),
        set_list_state=lambda *args: events.append(("status", args)),
    )
    view.update_action_availability = lambda: events.append("actions")
    view.host = NS(
        toast=toasts.append,
        open_book_detail=lambda *args, **kw: opens.append((args, kw)) or True,
    )
    return view, current, catalogue, alice, bob, events, toasts, opens


def test_switching_to_different_profile_revokes_selected_book_even_with_same_key():
    view, current, catalogue, alice, bob, events, _, opens = build_view()
    current[0] = "bob"
    view.render_cached()
    assert view.selected_book is None
    assert view._detail_selection_generation == 6
    assert view._last_cache_identity == "bob"
    assert view.rows == {book_key(bob): bob}
    assert catalogue.calls == ["bob"]
    assert events[0] == "empty"
    rows = next(item[1] for item in events if isinstance(item, tuple) and item[0] == "rows")
    assert [row.title for row in rows] == ["Bob's book"]
    view.open_selected()
    assert opens == []


def test_repeated_refresh_of_same_identity_does_not_revoke_current_selection():
    view, current, catalogue, alice, bob, events, _, _ = build_view()
    view.render_cached()
    assert view.selected_book is alice
    assert view._detail_selection_generation == 5
    assert "empty" not in events
    assert catalogue.calls == ["alice"]


def test_valid_to_no_profile_invalidates_selected_book_and_stops_catalogue_read():
    view, current, catalogue, alice, bob, events, _, _ = build_view()
    current[0] = ""
    view.render_cached()
    assert view.selected_book is None
    assert view._last_cache_identity == ""
    assert view._detail_selection_generation == 6
    assert catalogue.calls == []
    assert events.count("empty") == 2  # revoke, then empty-list placeholder


def test_return_to_original_profile_never_resurrects_previously_selected_book():
    view, current, catalogue, alice, bob, events, _, _ = build_view()
    current[0] = "bob"
    view.render_cached()
    current[0] = "alice"
    view.render_cached()
    assert view.selected_book is None
    assert view.rows == {book_key(alice): alice}
    assert view._detail_selection_generation == 7
    assert catalogue.calls == ["bob", "alice"]
