"""#1599: one poisoned bibliographic row must not erase the Constellations page.

Headless projection and synthetic metadata broker; no GTK/network/files.
"""
from types import SimpleNamespace as NS

from constellations_page import View, _usable_nodes
from constellation_view_model import Navigation


class StringBomb:
    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_SECRET_TOKEN")


def test_malformed_metadata_is_quarantined_without_leaking_raw_details():
    rows = (
        {"book_id": "book-1", "title": "First", "author": "A"},
        {"book_id": "bad", "title": StringBomb(), "metadata": "PRIVATE_BAD_METADATA"},
        {"book_id": "book-2", "title": "Second", "author": "B"},
    )
    nodes, skipped = _usable_nodes(rows, origin="bibliothèque")
    assert len(nodes) == 2
    assert skipped == 1
    assert [node.book.title for node in nodes] == ["First", "Second"]


def test_empty_or_bad_seed_does_not_abort_other_books():
    nodes, rejected = _usable_nodes(
        (None, {"book_id": "good", "title": "Good"}),
        origin="sélection",
    )
    assert len(nodes) == 1
    assert nodes[0].book.title == "Good"
    assert rejected == 1


def test_load_uses_valid_rows_and_returns_an_anonymized_rejection_count():
    rows = (
        {"book_id": "ok-1", "title": "A", "author": "Writer"},
        {"book_id": "bad-1", "title": "Broken", "metadata": "Bearer PRIVATE_TOKEN"},
        {"book_id": "ok-2", "title": "B", "author": "Writer"},
    )
    captured = []
    messages = []
    metadata = NS(library_entries=lambda **kwargs: rows)
    context = NS(broker=lambda name: metadata if name == "metadata" else None)
    view = object.__new__(View)
    view.plugin = NS(context=context, pending=None, pending_identity="")
    view.identity_id = lambda: "alice"
    view.cache = {}
    view.generation, view.task = 0, None
    view.nodes, view.local, view.center = (), (), None
    view.nav = Navigation()
    view.render = lambda: None
    view.status = NS(set_label=messages.append)
    view.run_calculation = lambda operation, complete, label: captured.append(
        operation(NS(cancelled=lambda: False))
    )
    view.load()
    assert len(captured) == 1
    cached_rows, local, nodes, seed_key, truncated, rejected = captured[0]
    assert cached_rows == rows
    assert len(local) == len(nodes) == 2
    assert seed_key is None and truncated is False and rejected == 1
    assert "PRIVATE" not in str(messages)


def test_loaded_renders_bounded_failure_count_not_raw_notice():
    messages = []
    view = object.__new__(View)
    view.disposed, view.generation = False, 4
    view.cache = {}
    view.plugin = NS(pending=None, pending_identity="")
    view.status = NS(set_label=messages.append)
    view.center, view.nodes, view.local = None, (), ()
    view.nav = Navigation()
    view.render = lambda: None
    row = NS(key="a")
    assert view.loaded(4, "alice", (({"book_id": "a"},), (row,), (row,), None, False, 1), "") is False
    assert "1 notice(s) non exploitables" in messages[-1]
    assert "PRIVATE" not in messages[-1]


def test_legacy_load_result_without_count_still_works():
    messages = []
    view = object.__new__(View)
    view.disposed, view.generation = False, 3
    view.cache = {}
    view.plugin = NS(pending=None, pending_identity="")
    view.status = NS(set_label=messages.append)
    view.center, view.nodes, view.local = None, (), ()
    view.nav = Navigation()
    view.render = lambda: None
    assert view.loaded(3, "alice", ((), (), (), None), "") is False
    assert "non exploitables" not in messages[-1]
