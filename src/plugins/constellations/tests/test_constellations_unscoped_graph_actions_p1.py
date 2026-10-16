"""#1599: an invalidated Constellations graph is not a source of book rights.

During load(), _nodes_identity becomes an empty string. Retained GTK
callbacks must not reopen a stale book merely because an empty scope
does not differ from the selected identity. All probes are in-memory.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from constellations_page import View
from tests.test_constellations_stale_history_p1 import fixture


def test_invalidated_graph_scope_rejects_visit_history_and_book_open():
    view, nodes, _, events = fixture()
    view._nodes_identity = ""  # load() invalidates scope during async reload
    opens = []
    view.host = NS(
        open_book_detail=lambda *args, **kwargs: opens.append((args, kwargs)) or True,
        toast=lambda message: opens.append(("toast", message)),
    )
    View.visit(view, nodes[0])
    View.move(view, -1)
    View.open_book(view)
    assert view.center is nodes[-1]
    assert view.nav.position == 2
    assert opens == [] and events == []


def test_stale_center_cannot_be_opened_even_after_same_profile_reload():
    view, nodes, _, events = fixture()
    opens = []
    view.host = NS(
        open_book_detail=lambda *args, **kwargs: opens.append((args, kwargs)) or True,
        toast=lambda message: opens.append(("toast", message)),
    )
    view.nodes = tuple(NS(key=node.key, book=node.book) for node in nodes)
    assert view.nodes[-1].key == view.center.key
    assert view.nodes[-1] is not view.center
    View.open_book(view)
    assert opens == [] and events == []
    view.center = view.nodes[-1]
    View.open_book(view)
    assert len(opens) == 1
    assert opens[0][1] == {"identity_id": "alice"}


def test_legacy_partial_bridge_without_scope_can_still_show_own_book():
    # Some old unit-level bridge fixtures are headless and omit the scope.
    # They are not allowed to bypass a *present but invalidated* scope.
    view, nodes, _, events = fixture()
    del view._nodes_identity
    view.host = NS(
        open_book_detail=lambda *args, **kwargs: True,
        toast=lambda _: None,
    )
    View.move(view, -1)
    assert view.center is nodes[1]
    assert events == ["cancel", "render"]
