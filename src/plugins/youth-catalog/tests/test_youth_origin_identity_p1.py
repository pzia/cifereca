"""#1599: 'Rechercher en Jeunesse' must honor the originating book identity."""
from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef
from youth_page import YouthPagePlugin, YouthView


class DropDown:
    def __init__(self, initial=0):
        self.index = initial
        self.changes = []

    def get_selected(self):
        return self.index

    def set_selected(self, index):
        self.index = index
        self.changes.append(index)


def fake_view(ids=("bob", "alice"), initial=0):
    view = object.__new__(YouthView)
    view.disposed = False
    view.identities = [{"identity_id": item} for item in ids]
    view.identity = DropDown(initial)
    query, rendered, status = [], [], []
    view.query = NS(set_text=query.append)
    view.render_cached = lambda: rendered.append(view.identity_id())
    view.status = NS(set_label=status.append)
    return view, query, rendered, status


def test_seed_switches_to_explicit_source_identity_before_filtering_cache():
    view, query, rendered, status = fake_view()
    book = BookRef("edition-1", "My book")
    assert view.seed(book, identity_id="alice") is True
    assert view.identity_id() == "alice"
    assert view.identity.changes == [1]
    assert query == ["My book"]
    assert rendered == ["alice"]
    assert status == []


def test_unknown_origin_never_injects_foreign_book_title():
    view, query, rendered, status = fake_view(ids=("bob",))
    book = BookRef("edition-1", "PRIVATE_TITLE")
    assert view.seed(book, identity_id="alice") is False
    assert view.identity_id() == "bob"
    assert query == [] and rendered == []
    assert len(status) == 1
    assert "PRIVATE_TITLE" not in status[0]
    assert "profil" in status[0]


def test_invalid_identity_dropdown_does_not_consume_origin():
    view, query, rendered, _ = fake_view(initial=0xFFFFFFFF)
    assert view.seed(BookRef("1", "Titre"), identity_id="alice") is True
    assert rendered == ["alice"]


def test_same_origin_keeps_dropdown_stable():
    view, query, rendered, _ = fake_view(initial=1)
    assert view.seed(BookRef("a", "Titre"), identity_id="alice")
    assert view.identity.changes == []
    assert rendered == ["alice"]


def test_closed_view_never_consumes_origin():
    view, query, rendered, _ = fake_view()
    view.disposed = True
    assert view.seed(BookRef("a", "PRIVATE"), identity_id="alice") is False
    assert query == [] and rendered == []


def test_plugin_passes_origin_identity_to_active_view():
    plugin = YouthPagePlugin().activate(NS())
    calls, navigations = [], []
    plugin.view = NS(
        seed=lambda book, **kwargs: calls.append((book, kwargs)) or True
    )
    book = BookRef("a", "Livre A")
    plugin.from_book(NS(
        action_context=lambda: NS(book=book, identity_id="alice"),
        navigate=navigations.append,
    ))
    assert calls == [(book, {"identity_id": "alice"})]
    assert plugin.pending is None and plugin.pending_identity == ""
    assert navigations == ["youth"]


def test_unavailable_origin_is_retained_for_recovery_but_never_cross_seeded():
    plugin = YouthPagePlugin().activate(NS())
    calls, navigations = [], []
    plugin.view = NS(
        seed=lambda book, **kwargs: calls.append((book, kwargs)) or False
    )
    book = BookRef("a", "PRIVATE_ORIGIN")
    plugin.from_book(NS(
        action_context=lambda: NS(book=book, identity_id="alice"),
        navigate=navigations.append,
    ))
    assert calls == [(book, {"identity_id": "alice"})]
    assert plugin.pending is book and plugin.pending_identity == "alice"
    assert navigations == ["youth"]


def test_legacy_context_without_identity_remains_navigation_only():
    plugin = YouthPagePlugin().activate(NS())
    navigations = []
    book = BookRef("a", "Livre A")
    plugin.from_book(NS(action_context=lambda: NS(book=book), navigate=navigations.append))
    assert plugin.pending is book
    assert plugin.pending_identity == ""
    assert navigations == ["youth"]
