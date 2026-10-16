"""P2 #1042 book-first Constellations UX: no arbitrary start or route planner."""
from __future__ import annotations

from types import SimpleNamespace as NS
import inspect

from cifereca.plugin_api import BookRef
from constellations_page import ConstellationsPlugin, View
from constellation_view_model import Navigation


class Label:
    def __init__(self):
        self.text = ""

    def set_label(self, text):
        self.text = str(text)


def test_direct_page_never_visits_arbitrary_first_known_book():
    first, second = NS(key="a"), NS(key="b")
    v = object.__new__(View)
    v.disposed, v.generation = False, 2
    v.cache, v.nav = {}, Navigation()
    v.status = Label()
    v.plugin = NS(pending=None)
    v.identity_id = lambda: 'alice'
    v.nodes, v.local, v.center = (), (), None
    snapshots = []
    v.render = lambda: snapshots.append(v.center)
    assert v.loaded(2, "alice", (("cached",), (first, second), (first, second), None), "") is False
    assert v.nodes == (first, second)
    assert v.center is None and v.nav.history == []
    assert snapshots == [None]
    assert v.plugin.pending is None
    assert "local" in v.status.text


def test_context_action_preserves_origin_and_uses_existing_public_entry_point():
    plugin = ConstellationsPlugin().activate(NS())
    book = BookRef("42", "Livre choisi")
    routes = []
    action = NS(book=book, identity_id="identity-2")
    plugin.pages()[0].actions[0].callback(
        NS(action_context=lambda: action, navigate=routes.append)
    )
    assert plugin.pending is book
    assert plugin.pending_identity == "identity-2"
    assert routes == ["constellations"]


def test_context_entry_is_non_network_and_origin_open_uses_same_identity():
    requested, info = [], []
    book = BookRef("42", "Origine")
    view = object.__new__(View)
    view.origin_book, view.origin_identity = book, "alice"
    view.identity_id = lambda: "bob"
    view.host = NS(
        open_book_detail=lambda candidate, **kwargs: requested.append(
            (candidate, kwargs["identity_id"])
        ) or True,
        toast=info.append,
    )
    view.open_origin()
    assert requested == [(book, "alice")]
    assert not info


def test_visited_neighbor_history_and_back_are_local():
    nodes = (NS(key="a"), NS(key="b"), NS(key="c"))
    view = object.__new__(View)
    view.nodes, view.nav = nodes, Navigation()
    view.center = None
    view.render = lambda: None
    view.cancel_calculation = lambda: None
    for node in nodes:
        view.visit(node)
    assert view.nav.history == ["a", "b", "c"]
    view.move(-1)
    assert view.center is nodes[1]
    view.move(-1)
    assert view.center is nodes[0]
    view.move(1)
    assert view.center is nodes[1]
    view.visit(nodes[2])
    assert view.nav.history == ["a", "b", "c"]


def test_mvp_has_no_walk_start_arrival_variants_or_implicit_remote_search():
    code = inspect.getsource(View.__init__)
    for legacy in ("Choisir le départ", "Rejoindre ce livre", "group('Parcours')",
                   "Chercher en ligne", "Mode de proximité", "Surprenant"):
        assert legacy not in code
    assert "Retour à la fiche d’origine" in code
    assert "← Livre précédent" in code
    assert "Découvrez des livres liés" in code
    render = inspect.getsource(View.render)
    assert "Choisir : " not in render
    assert "mode='Confort'" in render
    assert "limit=12" in render
