"""GTK startup card, isolated from MainWindow ownership (#1132).

The card is UI-only. Provider connections remain explicit, task-driven and
owned by MainWindow; constructing this view performs no network activity.
"""
from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .. import __version__
from .action_widgets import AdaptiveActionBar, add_css


def build_startup_page(window: Any) -> Gtk.Widget:
    """Construct the historical startup card without a reference cycle."""
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    outer.set_valign(Gtk.Align.CENTER)
    outer.set_halign(Gtk.Align.FILL)
    clamp = Adw.Clamp(); clamp.set_maximum_size(620); clamp.set_tightening_threshold(460); outer.append(clamp)
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14); add_css(card, "login-card"); clamp.set_child(card)
    icon = Gtk.Image.new_from_icon_name("accessories-dictionary-symbolic"); icon.set_pixel_size(52); icon.set_halign(Gtk.Align.START); card.append(icon)
    eyebrow = Gtk.Label(label="BIBLIOTHÈQUE MULTI-SOURCES", xalign=0); add_css(eyebrow, "dim-label", "caption"); card.append(eyebrow)
    title = Gtk.Label(label="cifereca", xalign=0); add_css(title, "login-brand"); card.append(title)
    subtitle = Gtk.Label(label="Connexion automatique aux providers configurés. Les identifiants sont gérés dans les préférences locales.", xalign=0)
    subtitle.set_wrap(True); add_css(subtitle, "dim-label"); card.append(subtitle)
    window.startup_status = Gtk.Label(label="Initialisation…", xalign=0); window.startup_status.set_wrap(True); card.append(window.startup_status)
    actions = AdaptiveActionBar(source=card)
    window.startup_connect_button = Gtk.Button(label="Connecter les sources"); window.startup_connect_button.add_css_class("suggested-action")
    window.startup_connect_button.connect("clicked", lambda _b: window.connect_configured_sources()); actions.append(window.startup_connect_button)
    settings = Gtk.Button(label="Configurer les sources"); settings.connect("clicked", lambda _b: window._open_prelogin_diagnostics()); actions.append(settings)
    card.append(actions)
    plugins = ", ".join(f"{d.display_name} ({'prêt' if d.state == 'ready' else 'squelette'})" for d in window.service.provider_descriptors()) or "aucun provider installé"
    info = Gtk.Label(label=f"Providers actifs : {plugins}", xalign=0); info.set_wrap(True); add_css(info, "caption", "dim-label"); card.append(info)
    version = Gtk.Label(label=f"v{__version__} · GTK 4 / libadwaita · Plugin API v1", xalign=0); add_css(version, "dim-label", "caption"); card.append(version)
    return outer
