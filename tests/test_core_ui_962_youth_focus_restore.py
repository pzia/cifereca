"""#962: targeted keyboard-focus restoration without importing GTK or making I/O."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.route_display_controller import show_route


class Button:
    def __init__(self, window):
        self.window = window
        self.mapped = True
        self.sensitive = True

    def get_mapped(self):
        return self.mapped

    def get_sensitive(self):
        return self.sensitive

    def grab_focus(self):
        self.window.focus = self
        return True


class YouthWidget:
    def __init__(self, window, button):
        self.window = window
        self.button = button
        self.default_focus_calls = 0

    def is_ancestor(self, candidate):
        return candidate is self.button

    def get_root(self):
        return self.window

    def child_focus(self, _direction):
        self.default_focus_calls += 1
        self.window.focus = object()
        return True


class Stack:
    def __init__(self, window):
        self.window = window
        self.visible = "youth"

    def get_visible_child_name(self):
        return self.visible

    def set_visible_child_name(self, name):
        if name != self.visible:
            # A different visible page may reset GTK focus; selecting the
            # already visible page is a no-op for keyboard focus.
            self.window.focus = object()
        self.visible = name


class Header:
    def set_label(self, value):
        self.value = value


class Host:
    def __init__(self):
        self._closed = False
        self._restoring_navigation = False
        self.focus = None
        self.view_stack = Stack(self)
        self.header_subtitle = Header()
        self._pages = {"youth": NS(title="Jeunesse"), "search": NS(title="Recherche")}
        self.service = NS(current_identity=NS(identity_id="camille"))
        self.button = Button(self)
        self.youth = YouthWidget(self, self.button)
        self._plugin_page_widgets = {"youth": self.youth}
        self._youth_return_focus = None
        self.header_calls = []

    def get_focus(self):
        return self.focus

    def _refresh_header_actions(self, route):
        self.header_calls.append(route)

    def _dispose_maintenance_lines(self):
        pass


Gtk = NS(Window=Host, Button=Button, DirectionType=NS(TAB_FORWARD=1))


def _outbound(host):
    host.focus = host.button
    show_route(host, "search", Gtk=Gtk)
    assert host.view_stack.visible == "search"


def test_back_restores_exact_click_origin_not_generic_first_filter():
    host = Host()
    _outbound(host)
    assert host._youth_return_focus == (host.button, "camille")
    host._restoring_navigation = True
    show_route(host, "youth", Gtk=Gtk)
    assert host.view_stack.visible == "youth"
    assert host.youth.default_focus_calls == 1
    assert host.get_focus() is host.button
    assert host._youth_return_focus is None


def test_other_identity_cannot_restore_old_accounts_action():
    host = Host()
    _outbound(host)
    host.service.current_identity.identity_id = "alex"
    host._restoring_navigation = True
    show_route(host, "youth", Gtk=Gtk)
    assert host.get_focus() is not host.button
    assert host._youth_return_focus is None


def test_disposed_or_detached_action_cannot_receive_restored_focus():
    for attr in ("mapped", "sensitive"):
        host = Host()
        _outbound(host)
        setattr(host.button, attr, False)
        host._restoring_navigation = True
        show_route(host, "youth", Gtk=Gtk)
        assert host.get_focus() is not host.button


def test_regular_navigation_back_to_youth_never_steals_focus():
    host = Host()
    _outbound(host)
    host._restoring_navigation = False
    show_route(host, "youth", Gtk=Gtk)
    assert host.get_focus() is not host.button


def test_switching_away_from_youth_without_a_focused_button_is_inert():
    host = Host()
    host.focus = object()
    show_route(host, "search", Gtk=Gtk)
    assert host._youth_return_focus is None
    host._restoring_navigation = True
    show_route(host, "youth", Gtk=Gtk)
    assert host.get_focus() is not host.button


def test_duplicate_show_route_during_back_does_not_replace_restored_action():
    host = Host()
    _outbound(host)
    host._restoring_navigation = True
    # NavigationState.restore() can call show_route via ListBox.select_row,
    # then once more explicitly with the same route.
    show_route(host, "youth", Gtk=Gtk)
    assert host.get_focus() is host.button
    show_route(host, "youth", Gtk=Gtk)
    assert host.get_focus() is host.button
    assert host.youth.default_focus_calls == 1


def test_gtk_idle_restoration_survives_double_render_and_late_focus_override():
    host = Host()
    _outbound(host)
    host._restoring_navigation = True
    callbacks = []
    show_route(host, "youth", Gtk=Gtk, idle_add=lambda callback: callbacks.append(callback))
    show_route(host, "youth", Gtk=Gtk, idle_add=lambda callback: callbacks.append(callback))
    host._restoring_navigation = False
    # Simulate GTK assigning keyboard focus after the synchronous callbacks.
    host.focus = object()
    assert len(callbacks) == 1
    assert callbacks.pop()() is False
    assert host.get_focus() is host.button


def test_deferred_focus_is_never_restored_to_stale_profile_or_route():
    for invalidation in ("profile", "route", "detached", "closed"):
        host = Host()
        _outbound(host)
        host._restoring_navigation = True
        callbacks = []
        show_route(host, "youth", Gtk=Gtk, idle_add=lambda callback: callbacks.append(callback))
        host._restoring_navigation = False
        host.focus = object()
        assert len(callbacks) == 1
        if invalidation == "profile":
            host.service.current_identity.identity_id = "alex"
        elif invalidation == "route":
            host.view_stack.visible = "search"
        elif invalidation == "detached":
            host.button.mapped = False
        else:
            host._closed = True
        assert callbacks.pop()() is False
        assert host.get_focus() is not host.button


def test_duplicate_search_route_keeps_clicked_action_until_history_return():
    host = Host()
    _outbound(host)
    assert host._youth_return_focus == (host.button, "camille")
    # The navigation shell emits two presentations, via select_row and
    # explicit show_route. This must not erase the user's initiating action.
    show_route(host, "search", Gtk=Gtk)
    assert host._youth_return_focus == (host.button, "camille")
    host._restoring_navigation = True
    callbacks = []
    show_route(host, "youth", Gtk=Gtk, idle_add=lambda callback: callbacks.append(callback))
    host._restoring_navigation = False
    host.focus = object()
    assert len(callbacks) == 1
    assert callbacks.pop()() is False
    assert host.get_focus() is host.button


def test_unrelated_route_after_search_invalidates_saved_youth_action():
    host = Host()
    _outbound(host)
    host._pages["unrelated"] = NS(title="Autre page")
    show_route(host, "unrelated", Gtk=Gtk)
    assert host._youth_return_focus is None
