"""#1599: homonymous Youth source records must not alias list/detail actions.

Synthetic cached books and host only. No GTK, remote requests or acquisitions.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from youth_page import FilterState, YouthView, _mapped_youth_entries
from tests.test_youth_page import youth_book


def _books():
    return (
        youth_book(record_id="same", title="Edition A",
                   identifiers={"isbn": "9780306406157", "source_item_id": "A"}),
        youth_book(record_id="same", title="Edition B",
                   identifiers={"isbn": "9780140328721", "source_item_id": "B"}),
        youth_book(record_id="same:1", title="Edition C",
                   identifiers={"isbn": "9782070360024", "source_item_id": "C"}),
        youth_book(record_id="same:2", title="Edition D",
                   identifiers={"source_item_id": "D"}),
    )


def test_generated_row_keys_never_shadow_future_real_source_keys():
    books = _books()
    indexed, entries = _mapped_youth_entries(books)
    keys = [row.key for row in entries]
    assert len(keys) == len(set(keys)) == len(books)
    assert keys == [
        "ecole-loisirs:same", "ecole-loisirs:same:3",
        "ecole-loisirs:same:1", "ecole-loisirs:same:2",
    ]
    assert all(indexed[row.key] is book for row, book in zip(entries, books))
    assert [row.title for row in entries] == [book.title for book in books]


def test_cache_render_and_detail_selection_agree_for_every_duplicate_edition():
    books = _books()
    captured_rows, shown, empty, queries = [], [], [], []
    view = object.__new__(YouthView)
    view.catalog = NS(search_cached=lambda *args, **kwargs: books)
    view.identity_id = lambda: "alice"
    view.filter_state = FilterState
    view._last_cache_identity = "alice"
    view.selected_book = None
    view.rows = {}
    view.update_action_availability = lambda: None
    view.master = NS(
        show_rows=lambda rows, **kwargs: captured_rows.extend(rows),
        show_detail=shown.append,
        empty_detail=lambda: empty.append(True),
        set_list_state=lambda *args, **kwargs: None,
    )
    view.host = NS(search_books=lambda *args, **kwargs: queries.append((args, kwargs)) or True)

    view.render_cached()
    assert len(captured_rows) == len(books) == len(view.rows)
    assert len({row.key for row in captured_rows}) == len(books)
    stale_actions = []
    for index, row in enumerate(captured_rows):
        if stale_actions:
            assert all(action.callback() is False for action in stale_actions)
        view.selected(row)
        assert view.selected_book is books[index]
        assert shown[-1].title == books[index].title
        assert shown[-1].subtitle == row.subtitle
        assert all(action.callback() is False for action in stale_actions)
        stale_actions.extend(shown[-1].actions)
    assert not queries
    assert empty == []


def test_single_uncollided_entry_preserves_original_public_key():
    book = youth_book(record_id="original")
    indexed, entries = _mapped_youth_entries([book])
    assert [r.key for r in entries] == ["ecole-loisirs:original"]
    assert indexed[entries[0].key] is book
