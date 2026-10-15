"""#1599: malformed context books must not mutate Constellations' active state.

Synthetic providers only; no GTK, HTTP, files or private exception strings.
"""
from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef
from constellations_page import ConstellationsPlugin, View
from constellation_view_model import node_from_row


class Poison:
    def __str__(self):
        raise RuntimeError("Bearer_PRIVATE_SECRET_ACCOUNT")


def make_view(*, identity="alice"):
    view = object.__new__(View)
    view.disposed = False
    view.identity_id = lambda: identity
    view.identities = [{"identity_id": "alice"}, {"identity_id": "bob"}]
    dropdown_changes = []
    view.identity = NS(set_selected=dropdown_changes.append)
    existing = node_from_row(BookRef("local", "Livre actuel"))
    view.nodes = (existing,)
    view.origin_book = BookRef("origin", "Origine")
    view.origin_identity = "alice"
    view.plugin = NS(pending="original_pending", pending_identity="alice")
    messages, visits, submissions = [], [], []
    view.status = NS(set_label=messages.append)
    view.visit = visits.append
    view.run_calculation = lambda *args: submissions.append(args)
    return view, dropdown_changes, messages, visits, submissions


def test_malformed_local_context_is_rejected_before_origin_or_graph_mutation():
    view, changes, messages, visits, submissions = make_view()
    previous = (view.origin_book, view.origin_identity, view.nodes, view.plugin.pending)
    assert view.seed({"title": Poison(), "metadata": "PRIVATE_PATH"}) is False
    assert (view.origin_book, view.origin_identity, view.nodes, view.plugin.pending) == previous
    assert changes == visits == submissions == []
    assert len(messages) == 1
    assert "PRIVATE" not in str(messages)


def test_foreign_bad_context_cannot_switch_identity_or_open_another_profile():
    view, changes, messages, visits, submissions = make_view()
    assert view.seed({"metadata": "PRIVATE_BROKEN"}, identity_id="bob") is False
    assert view.identity_id() == "alice"
    assert changes == visits == submissions == []
    assert view.plugin.pending == "original_pending"
    assert "PRIVATE" not in str(messages)


def test_wrong_shape_and_disposed_seeds_are_safe():
    view, changes, messages, visits, submissions = make_view()
    assert view.seed("PRIVATE_BAD_BOOK") is False
    view.disposed = True
    assert view.seed(BookRef("a", "Livre"), identity_id="bob") is False
    assert changes == visits == submissions == []
    assert "PRIVATE" not in str(messages)


def test_valid_known_central_book_remains_selectable_without_new_worker():
    view, changes, messages, visits, submissions = make_view()
    book = BookRef("local", "Livre actuel")
    assert view.seed(book) is True
    assert len(visits) == 1
    assert visits[0].book.title == "Livre actuel"
    assert submissions == []
    assert view.origin_book is book


def test_valid_book_for_other_identity_is_kept_pending_without_wrong_navigation():
    view, changes, messages, visits, submissions = make_view()
    book = BookRef("new", "Livre B")
    assert view.seed(book, identity_id="nobody") is True
    assert view.plugin.pending is book
    assert view.plugin.pending_identity == "nobody"
    assert changes == visits == submissions == []
    assert view.origin_identity == "alice"
    assert "absent" in messages[-1]


def test_plugin_action_clears_rejected_book_before_future_page_rebuild():
    plugin = ConstellationsPlugin().activate(NS())
    plugin.view = NS(seed=lambda *_args, **_kw: False)
    actions = []
    plugin.from_book(NS(
        action_context=lambda: NS(book={"metadata": "PRIVATE_BAD"}, identity_id="bob"),
        navigate=actions.append,
    ))
    assert plugin.pending is None and plugin.pending_identity == ""
    assert actions == ["constellations"]


def test_legacy_view_that_does_not_return_bool_remains_compatible():
    plugin = ConstellationsPlugin().activate(NS())
    plugin.view = NS(seed=lambda *_args, **_kw: None)
    book = BookRef("a", "Livre A")
    plugin.from_book(NS(
        action_context=lambda: NS(book=book, identity_id="alice"),
        navigate=lambda *_: None,
    ))
    assert plugin.pending is book and plugin.pending_identity == "alice"
