"""GTK-independent wiring of Settings dirty revisions and action buttons.

The host still owns revisions, Gtk/Adw classes and the SettingsPageLifetime.
No Core writes, provider calls or signals are registered outside this explicit
builder. A disposed page rejects late edit notifications.
"""
from __future__ import annotations

from typing import Any

from .settings_context import settings_action_state


def bind_settings_actions(*, tabs, sections, settings_state, page_lifetime, Gtk, Adw):
    tracked_settings_widgets: set[int] = set()
    settings_action_widgets: dict[str, Any] = {}

    def current_settings_tab() -> str:
        return str(tabs.get_visible_child_name() or "accounts")

    def update_settings_actions(*_args) -> None:
        if not page_lifetime.alive:
            return
        tab = current_settings_tab()
        state = settings_action_state(
            tab,
            dirty=settings_state.dirty(tab),
        )
        save_widgets = tuple(settings_action_widgets.get("save", ()))
        reconnect_widgets = tuple(settings_action_widgets.get("reconnect", ()))
        for widget in save_widgets:
            widget.set_visible(state.show_save)
            widget.set_sensitive(state.save_sensitive)
        for widget in reconnect_widgets:
            widget.set_visible(state.show_reconnect)
            widget.set_sensitive(state.reconnect_sensitive)
        for widget in tuple(settings_action_widgets.get("containers", ())):
            widget.set_visible(state.show_save or state.show_reconnect)

    def mark_settings_dirty(tab: str) -> None:
        if not page_lifetime.alive:
            return
        if not settings_state.mark_dirty(tab):
            return
        update_settings_actions()

    def track_settings_tree(tab: str, widget: Gtk.Widget) -> None:
        marker = id(widget)
        if marker not in tracked_settings_widgets:
            signal = None
            if isinstance(widget, Adw.EntryRow):
                signal = "notify::text"
            elif isinstance(widget, Gtk.Switch):
                signal = "notify::active"
            elif isinstance(widget, Gtk.DropDown):
                signal = "notify::selected"
            if signal is not None:
                widget.connect(signal, lambda *_args, tab_name=tab: mark_settings_dirty(tab_name))
                tracked_settings_widgets.add(marker)
        try:
            child = widget.get_first_child()
        except AttributeError:
            child = None
        while child is not None:
            track_settings_tree(tab, child)
            child = child.get_next_sibling()

    def track_all_settings_widgets() -> None:
        for tab, section in sections.items():
            if tab != "diagnostic":
                track_settings_tree(tab, section)

    return update_settings_actions, mark_settings_dirty, track_all_settings_widgets, settings_action_widgets
