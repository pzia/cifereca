"""Purely UI composition of the dashboard's persistent page (#1051).

The MainWindow owns the page and its actions. This module does not import
PyGObject, start synchronization, or subscribe to plugin signals.
"""
from __future__ import annotations


def build_dashboard_page(window, *, Gtk, add_css, AdaptiveActionBar):
    if getattr(window, "_closed", False):
        return None
    scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    outer.set_margin_top(24); outer.set_margin_bottom(24); outer.set_margin_start(24); outer.set_margin_end(24)
    heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); outer.append(heading)
    title = Gtk.Label(label="Bibliothèque familiale", xalign=0); title.set_hexpand(True); title.set_wrap(True); add_css(title, "page-title"); heading.append(title)
    subtitle = Gtk.Label(label="État des identités et de la synchronisation automatique", xalign=0); add_css(subtitle, "dim-label"); outer.append(subtitle)
    window.dashboard_loading_status = Gtk.Label(label="Connexion aux sources en arrière-plan…", xalign=0)
    window.dashboard_loading_status.set_wrap(True); add_css(window.dashboard_loading_status, "dim-label", "caption")
    outer.append(window.dashboard_loading_status)
    actions = AdaptiveActionBar(source=heading)
    refresh = window.action_button("Synchronisation rapide", "view-refresh-symbolic", suggested=True)
    refresh.connect("clicked", lambda _b: window.run_opening_sync(refresh, deep=False)); actions.append(refresh)
    deep = window.action_button("Vérifier toute la bibliothèque", "system-search-symbolic")
    deep.connect("clicked", lambda _b: window.run_opening_sync(deep, deep=True)); actions.append(deep)
    actions.set_halign(Gtk.Align.END); heading.append(actions)
    window.dashboard_cards = Gtk.FlowBox(); window.dashboard_cards.set_selection_mode(Gtk.SelectionMode.NONE)
    window.dashboard_cards.set_min_children_per_line(1); window.dashboard_cards.set_max_children_per_line(3)
    window.dashboard_cards.set_homogeneous(True); window.dashboard_cards.set_column_spacing(12); window.dashboard_cards.set_row_spacing(12)
    outer.append(window.dashboard_cards)
    scroll.set_child(outer)
    return scroll
