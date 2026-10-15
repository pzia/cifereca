"""#1599: stale neighbour actions must not select a book in another graph.

The synthetic callbacks model GTK retaining a clicked button during graph
reload, identity change or A→B→A visits. No network, GTK or Core mutation.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef
from constellation_view_model import Navigation, node_from_row
from constellations_page import View


def fixture():
    view = object.__new__(View)
    view.disposed = False
    view.profile = "alice"
    view.identity_id = lambda: view.profile
    view._nodes_identity = "alice"
    a = node_from_row(BookRef("edition-a", "Titre A"))
    b = node_from_row(BookRef("edition-b", "Titre B"))
    view.nodes = (a, b)
    view.center = a
    view.nav = Navigation()
    view.nav.visit(a.key)
    calls = []
    view.cancel_calculation = lambda: calls.append("cancel")
    view.render = lambda: calls.append("render")
    view.host = NS(
        open_book_detail=lambda book, **kwargs: calls.append(("open", book, kwargs)) or True,
        toast=lambda value: calls.append(("toast", value)),
    )
    return view, a, b, calls


def test_current_neighbour_can_be_visited_normally_and_forward_history_works():
    view, a, b, calls = fixture()
    View.visit(view, b)
    assert view.center is b
    assert view.nav.history == [a.key, b.key]
    assert calls == ["cancel", "render"]


def test_stale_callback_after_graph_reload_cannot_replace_current_book():
    view, old_a, old_b, calls = fixture()
    old_callback = lambda: View.visit(view, old_b)
    # A newly loaded book with the *same key* is still a different graph node.
    new_a = node_from_row(BookRef("edition-a", "Titre A"))
    new_b = node_from_row(BookRef("edition-b", "Titre B"))
    assert new_b.key == old_b.key and new_b is not old_b
    view.nodes = (new_a, new_b)
    view.center = new_a
    old_callback()
    assert view.center is new_a
    assert calls == []
    View.visit(view, new_b)
    assert view.center is new_b
    assert calls == ["cancel", "render"]


def test_identity_change_blocks_retained_neighbour_and_book_open_action():
    view, a, b, calls = fixture()
    # The graph has not yet been reset by the GTK profile-change callback.
    view.profile = "bob"
    View.visit(view, b)
    View.open_book(view)
    assert view.center is a
    assert calls == []
    # A fresh Bob-scoped graph may again navigate normally, never implicitly
    # carrying over Alice's prior page or local-book entitlement.
    bob = node_from_row(BookRef("bob-edition", "Livre Bob"))
    view.nodes, view.center, view._nodes_identity = (bob,), bob, "bob"
    View.open_book(view)
    assert calls == [("open", bob.book, {"identity_id": "bob"})]


def test_invalidated_graph_cannot_be_restored_by_old_callback_after_load():
    view, a, b, calls = fixture()
    old_callback = lambda: View.visit(view, b)
    view.profile = "bob"
    view._nodes_identity = ""  # load() invalidates scope before async work
    view.nodes = ()
    view.center = None
    old_callback()
    assert view.center is None
    assert calls == []


def test_close_blocks_any_retained_neighbour_action():
    view, a, b, calls = fixture()
    view.disposed = True
    View.visit(view, b)
    View.open_book(view)
    assert view.center is a and calls == []
