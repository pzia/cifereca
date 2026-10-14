"""Recommendation actions: public routes, stale services, and closed windows."""
from types import SimpleNamespace as NS

from cifereca.gui.book_recommendation_controller import add_book_recommendation_action


class Button:
    def __init__(self):
        self.handlers = {}

    def connect(self, name, callback):
        self.handlers[name] = callback

    def click(self):
        self.handlers["clicked"](self)


def _context(*, available=True, route="recommendations/new", failure=None):
    calls = []
    button = Button()
    service = NS(recommend_from_book=lambda book: (
        (_ for _ in ()).throw(failure) if failure else route
    ))
    target = NS(book_id="book-1")
    window = NS(
        _closed=False,
        _book_recommendation_target=lambda item: (
            (service, target) if available else None
        ),
        action_button=lambda label, icon: button,
        toast=lambda message: calls.append(("toast", message)),
        show_error=lambda message, title: calls.append(("error", title)),
        navigate=lambda route: calls.append(("navigate", route)),
    )
    detail = NS(add_actions=lambda buttons: calls.append(("actions", buttons)))
    return window, detail, button, calls


def test_no_capability_never_adds_button():
    window, detail, button, calls = _context(available=False)
    add_book_recommendation_action(window, detail, {})
    assert calls == []


def test_recommendation_uses_exact_public_route():
    window, detail, button, calls = _context(route="plugin/custom/detail")
    add_book_recommendation_action(window, detail, {})
    assert button._cifereca_action_id == "recommend-from-book"
    assert button._cifereca_action_priority == 25
    button.click()
    assert calls[1] == ("navigate", "plugin/custom/detail")


def test_service_removed_between_render_and_click():
    window, detail, button, calls = _context()
    add_book_recommendation_action(window, detail, {})
    window._book_recommendation_target = lambda item: None
    button.click()
    assert calls[1][0] == "toast"
    assert not any(x[0] == "navigate" for x in calls)


def test_plugin_failure_and_empty_route_do_not_navigate():
    for route, failure in (("", None), ("ignored", RuntimeError("offline"))):
        window, detail, button, calls = _context(route=route, failure=failure)
        add_book_recommendation_action(window, detail, {})
        button.click()
        assert calls[1][0] == "error"
        assert not any(x[0] == "navigate" for x in calls)


def test_late_click_after_window_close_is_ignored():
    window, detail, button, calls = _context()
    add_book_recommendation_action(window, detail, {})
    window._closed = True
    button.click()
    assert len(calls) == 1


def test_closed_window_never_resolves_service_or_creates_button():
    window, detail, button, calls = _context()
    window._closed = True
    window._book_recommendation_target = lambda item: (_ for _ in ()).throw(
        AssertionError("service accessed after close")
    )
    add_book_recommendation_action(window, detail, {})
    assert calls == []