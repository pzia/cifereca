"""#1599: contextual detail selection never crosses Constellations identities.

Headless public BookRef fixtures; no GTK, network, accounts or file reads.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef
from constellations_page import View
from constellation_view_model import node_from_row


def _view(known=("bob",)):
    view = object.__new__(View)
    messages, called = [], []
    view.disposed = False
    view.identities = [{"identity_id": ident} for ident in known]
    selection = [0]
    view.identity_id = lambda: known[selection[0]]
    view.status = NS(set_label=messages.append)
    view.plugin = NS(pending=None, pending_identity="")
    old = BookRef("old-b", "Livre de Bob", ("Bob",))
    view.origin_book = old
    view.origin_identity = "bob"
    view.nodes = (node_from_row(old),)
    view.center = view.nodes[0]
    view.run_calculation = lambda *_args, **_kw: called.append("WRONG_GRAPH")
    view.visit = lambda *_args: called.append("WRONG_VISIT")
    view.load = lambda: called.append(("load", view.identity_id(),
                                      view.plugin.pending_identity))
    view.identity = NS(
        get_selected=lambda: selection[0],
        set_selected=lambda next_index: (
            selection.__setitem__(0, next_index),
            view.load(),
        ),
    )
    return view, messages, called


def test_context_seed_for_absent_profile_stays_pending_without_crossing_identity():
    view, messages, calls = _view()
    foreign = BookRef("edition-a", "Livre privé de Alice", ("Alice",))
    old_center, old_origin = view.center, view.origin_book

    view.seed(foreign, identity_id="alice")

    assert calls == []  # no graph mutation, navigation, task or reload for Bob
    assert view.identity_id() == "bob"
    assert view.center is old_center
    assert view.nodes == (old_center,)
    assert view.origin_book is old_origin
    assert view.origin_identity == "bob"
    assert view.plugin.pending is foreign
    assert view.plugin.pending_identity == "alice"
    assert messages == [
        "Profil du livre absent de cette liste ; le voisinage actuel est conservé."
    ]
    assert "Alice" not in messages[0]


def test_matching_profile_selection_sets_pending_scope_before_reloading():
    view, messages, calls = _view(("bob", "alice"))
    foreign = BookRef("edition-a", "Même titre", ("Alice",))

    view.seed(foreign, identity_id="alice")

    assert view.identity_id() == "alice"
    assert view.plugin.pending is foreign
    assert view.plugin.pending_identity == "alice"
    assert view.origin_book is foreign
    assert view.origin_identity == "alice"
    assert calls == [("load", "alice", "alice")]
    assert messages == []


def test_stale_seed_after_plugin_disposal_never_changes_pending_or_identity():
    view, messages, calls = _view()
    view.disposed = True
    foreign = BookRef("edition-a", "Livre", ("Alice",))

    view.seed(foreign, identity_id="alice")

    assert view.plugin.pending is None
    assert view.plugin.pending_identity == ""
    assert view.identity_id() == "bob"
    assert calls == messages == []
    assert view.origin_identity == "bob"


def test_legacy_unscoped_same_profile_seed_preserves_normal_navigation():
    view, messages, calls = _view()
    original = BookRef("book-b", "Livre Bob", ("Bob",))
    current = node_from_row(original)
    view.nodes = (current,)
    view.center = current

    view.seed(original, identity_id="bob")

    # The view reuses a matching book without submitting any expensive
    # calculation or switching identity.
    assert calls == ["WRONG_VISIT"]
    assert view.origin_book is original
    assert view.origin_identity == "bob"
    assert view.plugin.pending is None
    assert view.identity_id() == "bob"
    assert messages == []
