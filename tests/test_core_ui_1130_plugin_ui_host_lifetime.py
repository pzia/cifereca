"""#1130: public plugin UI host lifetime and detached action callbacks (headless)."""
from __future__ import annotations

from types import SimpleNamespace as NS

from tests.test_plugin_ui_host_boundary import host_type


class Button:
    def __init__(self):
        self.handler = None

    def connect(self, signal, handler):
        assert signal == "clicked"
        self.handler = handler

    def click(self):
        assert self.handler is not None
        self.handler(self)


class Bar:
    def __init__(self):
        self.children = []

    def append(self, widget):
        self.children.append(widget)


def _host(window):
    return host_type(
        AdaptiveActionBar=Bar,
        action_button=lambda title, icon: Button(),
    )(window)


def test_detached_plugin_page_action_is_revoked_by_shell_generation_change():
    token_one, token_two = object(), object()
    window = NS(_closed=False, _shell_page_rebuild_token=token_one)
    host = _host(window)
    calls = []
    old = host.build_action_bar([
        NS(title="Open", icon_name="system-search",
           callback=lambda owner: calls.append(owner)),
    ]).children[0]
    old.click()
    assert calls == [host]
    window._shell_page_rebuild_token = token_two
    old.click()
    assert calls == [host]
    fresh = _host(window)
    fresh.build_action_bar([
        NS(title="Open", icon_name="system-search",
           callback=lambda owner: calls.append(owner)),
    ]).children[0].click()
    assert calls == [host, fresh]


def test_closed_owner_ignores_queued_button_even_without_shell_generation():
    window = NS(_closed=False)
    host = _host(window)
    calls = []
    button = host.build_action_bar([
        NS(title="Go", icon_name="", callback=lambda caller: calls.append(caller)),
    ]).children[0]
    window._closed = True
    button.click()
    assert calls == []


def test_library_refresh_closing_window_prevents_following_recent_refresh():
    calls = []
    window = NS(_closed=False)
    def library(scan):
        calls.append(("library", scan))
        window._closed = True
    window.refresh_library = library
    window.refresh_recent = lambda: calls.append(("recent",))
    from cifereca.plugin_api import UiActionContext
    host = _host(window)
    host._action_context = UiActionContext(target="page", route="library")
    host.refresh_context()
    assert calls == [("library", False)]


def test_navigate_closing_window_prevents_plugin_focus():
    calls = []
    window = NS(_closed=False)
    def navigate(route):
        calls.append(("navigate", route))
        window._closed = True
    window.navigate = navigate
    window.plugins_page = NS(focus_plugin=lambda ident: calls.append(("focus", ident)))
    _host(window).open_plugin_settings("private-provider")
    assert calls == [("navigate", "plugins")]


def test_reentrant_navigation_to_another_route_does_not_focus_plugin_manager():
    calls = []
    window = NS(_closed=False)
    visible = ["plugins"]
    window.view_stack = NS(get_visible_child_name=lambda: visible[0])
    def navigate(route):
        calls.append(("navigate", route))
        visible[0] = "settings"
    window.navigate = navigate
    window.plugins_page = NS(focus_plugin=lambda ident: calls.append(("focus", ident)))
    _host(window).open_plugin_settings("provider")
    assert calls == [("navigate", "plugins")]


def test_live_plugin_settings_focus_after_successful_navigation():
    calls = []
    window = NS(
        _closed=False,
        navigate=lambda route: calls.append(("navigate", route)),
        view_stack=NS(get_visible_child_name=lambda: "plugins"),
        plugins_page=NS(focus_plugin=lambda ident: calls.append(("focus", ident))),
    )
    _host(window).open_plugin_settings("reader")
    assert calls == [("navigate", "plugins"), ("focus", "reader")]


def test_unsubscribe_identifies_callback_by_identity_not_custom_equality():
    class EqualCallable:
        def __init__(self, tag):
            self.tag = tag
        def __call__(self):
            return None
        def __eq__(self, other):
            return isinstance(other, EqualCallable)
    a, b = EqualCallable("a"), EqualCallable("b")
    window = NS(_closed=False)
    host = _host(window)
    host.subscribe_identity_overview(a)
    cancel_b = host.subscribe_identity_overview(b)
    assert len(window._overview_listeners) == 2
    cancel_b()
    cancel_b()
    assert len(window._overview_listeners) == 1
    assert window._overview_listeners[0] is a


def test_cancel_old_replaced_overview_listener_list_does_not_touch_new_generation():
    window = NS(_closed=False)
    host = _host(window)
    old = lambda: None
    current = lambda: None
    cancel_old = host.subscribe_identity_overview(old)
    window._overview_listeners = [current]
    cancel_old()
    assert window._overview_listeners == [current]
