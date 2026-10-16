"""#1599: retained history buttons cannot resurrect a foreign book detail.

In-memory graph and navigation only; no GTK, provider, accounts or network.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from constellation_view_model import Navigation
from constellations_page import View


def fixture():
    view = object.__new__(View)
    view.disposed = False
    view._nodes_identity = "alice"
    profile = ["alice"]
    view.identity_id = lambda: profile[0]
    nodes = tuple(NS(key=k, book=NS(title=k.upper())) for k in ("a", "b", "c"))
    view.nodes = nodes
    view.nav = Navigation()
    for node in nodes:
        view.nav.visit(node.key)
    view.center = nodes[-1]
    events = []
    view.cancel_calculation = lambda: events.append("cancel")
    view.render = lambda: events.append("render")
    return view, nodes, profile, events


def test_normal_back_forward_history_selects_only_current_graph_nodes():
    view, nodes, _, events = fixture()
    View.move(view, -1)
    assert view.center is nodes[1]
    View.move(view, -1)
    assert view.center is nodes[0]
    View.move(view, +1)
    assert view.center is nodes[1]
    assert view.nav.position == 1
    assert events == ["cancel", "render"] * 3


def test_retained_back_forward_callbacks_ignore_identity_switch_before_reload():
    view, nodes, profile, events = fixture()
    back, forward = lambda: View.move(view, -1), lambda: View.move(view, +1)
    profile[0] = "bob"  # GTK has changed, the old graph has not been cleared yet
    back()
    forward()
    assert view.center is nodes[-1]
    assert view.nav.position == 2
    assert events == []


def test_stale_center_with_same_book_key_cannot_navigate_new_graph():
    view, old, _, events = fixture()
    new = tuple(NS(key=k, book=NS(title=k.upper())) for k in ("a", "b", "c"))
    view.nodes = new
    assert view.center is old[-1] and new[-1].key == old[-1].key
    View.move(view, -1)
    assert view.center is old[-1]
    assert view.nav.position == 2
    assert events == []


def test_history_key_not_in_current_graph_does_not_render_previous_book():
    view, nodes, _, events = fixture()
    view.nodes = nodes[:2]
    view.center = nodes[1]
    view.nav.position = 1
    View.move(view, +1)
    assert view.center is nodes[1]
    assert view.nav.position == 1
    assert events == []


def test_disposed_history_callback_does_not_touch_identity_or_graph():
    view, nodes, _, events = fixture()
    view.disposed = True
    view.identity_id = lambda: (_ for _ in ()).throw(AssertionError("read after close"))
    View.move(view, -1)
    assert view.center is nodes[-1]
    assert events == []



def test_graph_invalidated_before_reload_blocks_all_current_book_actions():
    view, nodes, _, events = fixture()
    view._nodes_identity = ""  # load() invalidated the graph authority
    opens = []
    view.host = NS(
        open_book_detail=lambda *args, **kwargs: opens.append((args, kwargs)) or True,
        toast=lambda msg: opens.append(("toast", msg)),
    )
    View.visit(view, nodes[0])
    View.move(view, -1)
    View.open_book(view)
    assert view.center is nodes[-1]
    assert view.nav.position == 2
    assert opens == [] and events == []


def test_book_open_rejects_stale_center_with_same_key_after_graph_reload():
    view, nodes, _, events = fixture()
    opens = []
    view.host = NS(
        open_book_detail=lambda *args, **kwargs: opens.append((args, kwargs)) or True,
        toast=lambda msg: opens.append(("toast", msg)),
    )
    # A delayed button callback holds on to a replaced object. A key is
    # insufficient to establish the library identity of an edition.
    view.nodes = tuple(NS(key=node.key, book=node.book) for node in nodes)
    assert view.nodes[-1].key == view.center.key
    assert view.nodes[-1] is not view.center
    View.open_book(view)
    assert opens == [] and events == []
    view.center = view.nodes[-1]
    View.open_book(view)
    assert len(opens) == 1
    assert opens[0][1] == {"identity_id": "alice"}
