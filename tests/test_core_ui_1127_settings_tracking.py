"""P0 #1127: Settings dirty tracking is reusable and has one signal per widget.

Headless: fake Gtk/Adw classes; no server, account mutation or PyGObject import.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.settings_lifetime import SettingsPageLifetime
from cifereca.gui.settings_model import SettingsRevisionState
from cifereca.gui.settings_tracking import bind_settings_actions


class Widget:
    def __init__(self, *children):
        self.children = list(children)
        self.next = None
        self.connections = []
        for left, right in zip(self.children, self.children[1:]):
            left.next = right

    def get_first_child(self):
        return self.children[0] if self.children else None

    def get_next_sibling(self):
        return self.next

    def connect(self, signal, callback):
        self.connections.append((signal, callback))

    def emit(self, signal):
        for event, callback in self.connections:
            if event == signal:
                callback(self)


class EntryRow(Widget):
    pass


class Switch(Widget):
    pass


class DropDown(Widget):
    pass


class Button:
    def __init__(self):
        self.visible = None
        self.sensitive = None

    def set_visible(self, value):
        self.visible = value

    def set_sensitive(self, value):
        self.sensitive = value


def setup_binding():
    entry = EntryRow()
    switch = Switch()
    dropdown = DropDown()
    diagnostic = EntryRow()
    sections = {
        "accounts": Widget(entry, Widget(switch, dropdown)),
        "sync": Widget(),
        "diagnostic": Widget(diagnostic),
    }
    current = ["accounts"]
    tabs = NS(get_visible_child_name=lambda: current[0])
    lifetime = SettingsPageLifetime()
    state = SettingsRevisionState(sections)
    update, mark, track, buttons = bind_settings_actions(
        tabs=tabs, sections=sections, settings_state=state,
        page_lifetime=lifetime,
        Gtk=NS(Widget=Widget, Switch=Switch, DropDown=DropDown),
        Adw=NS(EntryRow=EntryRow),
    )
    buttons["save"] = (Button(), Button())
    buttons["reconnect"] = (Button(),)
    buttons["containers"] = (Button(),)
    return NS(
        entry=entry, switch=switch, dropdown=dropdown, diagnostic=diagnostic,
        current=current, lifetime=lifetime, state=state,
        update=update, mark=mark, track=track, buttons=buttons,
    )


def test_tracking_is_one_subscription_per_widget_and_excludes_diagnostics():
    host = setup_binding()
    host.track()
    host.track()  # rebuilding bindings must not duplicate signals
    assert [s for s, _ in host.entry.connections] == ["notify::text"]
    assert [s for s, _ in host.switch.connections] == ["notify::active"]
    assert [s for s, _ in host.dropdown.connections] == ["notify::selected"]
    assert host.diagnostic.connections == []

    host.update()
    assert host.buttons["save"][0].visible and not host.buttons["save"][0].sensitive
    assert host.buttons["reconnect"][0].visible
    host.entry.emit("notify::text")
    assert host.state.dirty("accounts")
    assert all(b.sensitive for b in host.buttons["save"])


def test_tab_visibility_reconnect_and_save_use_only_current_revision():
    host = setup_binding()
    host.update()
    host.current[0] = "sync"
    host.update()
    assert host.buttons["reconnect"][0].visible
    assert not host.buttons["save"][0].sensitive
    host.mark("sync")
    assert host.buttons["save"][0].sensitive
    host.current[0] = "diagnostic"
    host.update()
    assert not host.buttons["reconnect"][0].visible
    assert not host.buttons["save"][0].visible
    assert not host.buttons["containers"][0].visible


def test_callbacks_are_inert_after_settings_page_dispose():
    host = setup_binding()
    host.track()
    host.update()
    initial = host.state.snapshot()
    before = (host.buttons["save"][0].visible, host.buttons["save"][0].sensitive)
    host.lifetime.dispose()
    host.entry.emit("notify::text")
    host.mark("accounts")
    host.current[0] = "sync"
    host.update()
    assert host.state.snapshot() == initial
    assert (host.buttons["save"][0].visible, host.buttons["save"][0].sensitive) == before
