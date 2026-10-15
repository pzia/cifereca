"""#1599: GTK callbacks retained after plugin deactivation must be inert."""
from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef
from constellations_page import View


def closed_view():
    view = object.__new__(View)
    view.disposed = True
    calls = []
    view.identity_id = lambda: calls.append("identity") or "alice"
    view.host = NS(
        open_book_detail=lambda *a, **kw: calls.append(("open", a, kw)) or True,
        toast=lambda msg: calls.append(("toast", msg)),
    )
    view.cancel_calculation = lambda: calls.append("cancel")
    view.render = lambda: calls.append("render")
    view.plugin = NS(context=NS(broker=lambda *a: calls.append("broker")))
    view.center = NS(book=BookRef("a", "A"), key="a")
    view.origin_book = BookRef("origin", "Origin")
    view.origin_identity = "alice"
    view.nodes = (view.center,)
    view.nav = NS(visit=lambda k: calls.append("navvisit"), move=lambda n: calls.append("navmove"))
    return view, calls


def test_after_close_no_navigation_or_broker_or_render_from_stale_actions():
    view, calls = closed_view()
    view.load()
    view.visit(view.center)
    view.move(-1)
    view.open_book()
    view.open_origin()
    assert calls == []


def test_normal_active_open_book_uses_explicit_current_identity():
    view, calls = closed_view()
    view.disposed = False
    view.open_book()
    assert calls == ["identity", ("open", (view.center.book,), {"identity_id": "alice"})]


def test_normal_active_return_preserves_original_book_and_profile():
    view, calls = closed_view()
    view.disposed = False
    view.origin_identity = "bob"
    view.open_origin()
    assert calls == [("open", (view.origin_book,), {"identity_id": "bob"})]


def test_dispose_revokes_actions_even_if_external_callback_keeps_view_alive():
    view = object.__new__(View)
    calls = []
    view.disposed = False
    view.generation = 4
    view.task = NS(cancel=lambda: calls.append("cancel"))
    view.unsubscribe = lambda: calls.append("unsubscribe")
    view.cache = {"alice": "PRIVATE_BOOK"}
    view.host = NS(open_book_detail=lambda *a, **k: calls.append("open") or True)
    view.center = NS(book=BookRef("a", "A"))
    view.origin_book = BookRef("a", "Origin")
    view.origin_identity = "alice"
    view.identity_id = lambda: "alice"
    callbacks = (view.open_book, view.open_origin)
    view.dispose()
    assert view.disposed is True and view.generation == 5
    assert view.cache == {}
    for callback in callbacks:
        callback()
    assert calls == ["cancel", "unsubscribe"]


def test_disposed_load_does_not_consult_even_an_invalid_dropdown():
    view = object.__new__(View)
    view.disposed = True
    view.identity_id = lambda: (_ for _ in ()).throw(AssertionError("read GTK after close"))
    assert view.load() is None
