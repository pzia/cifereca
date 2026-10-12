"""Extracted GTK Settings shell and tabs, presentation only (P0 #1127).

No GTK dependency at import time: the current MainWindow host injects classes.
The builder creates the identical scroll/clamp/tab layout and owns no signals,
dirty state, persistence, remote access, or cleanup.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SettingsShell:
    scroll: Any
    root: Any
    heading: Any
    tabs: Any
    sections: dict[str, Any]


def build_settings_shell(*, Gtk: Any, Adw: Any, add_css: Callable[..., Any]) -> SettingsShell:
    """Construct original viewport and six named sections without callbacks."""
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    clamp = Adw.Clamp(); clamp.set_maximum_size(980); scroll.set_child(clamp)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    box.set_margin_top(22); box.set_margin_bottom(28); box.set_margin_start(18); box.set_margin_end(18)
    clamp.set_child(box)
    settings_heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); box.append(settings_heading)
    title = Gtk.Label(label="Configuration et diagnostic", xalign=0); title.set_hexpand(True); title.set_wrap(True); add_css(title, "page-title"); settings_heading.append(title)
    sub = Gtk.Label(label="Identités, sources, acquisitions automatiques, intégration KOBO-ADEPT et diagnostic.", xalign=0); add_css(sub, "dim-label"); box.append(sub)
    settings_root = box
    tabs = Gtk.Stack(); tabs.set_hexpand(True)
    switcher = Gtk.StackSwitcher(); switcher.set_stack(tabs); switcher.add_css_class("settings-tabs")
    tab_scroll = Gtk.ScrolledWindow(); tab_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    tab_scroll.set_child(switcher); settings_root.append(tab_scroll); settings_root.append(tabs)
    sections = {}
    for key, label in (("accounts", "Identités et comptes"), ("library", "Bibliothèque"),
                       ("sync", "Synchronisation"), ("interface", "Interface"),
                       ("ai", "Intelligence artificielle"), ("diagnostic", "Diagnostic")):
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sections[key] = section; tabs.add_titled(section, key, label)
    return SettingsShell(scroll=scroll, root=settings_root, heading=settings_heading, tabs=tabs, sections=sections)
