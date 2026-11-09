"""P1 #757: V2 prototype integration remains scoped, keyboard-safe and reversible.

GTK-free harness for exact appearance transitions; this intentionally does
not claim a real-GTK screenshot or final UX acceptance.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.modern_light_theme import (
    MODERN_LIGHT_CLASS, MODERN_LIGHT_CSS, install_modern_light,
    modern_light_enabled, release_modern_light, update_light_class,
)
from cifereca.gui.shell_stylesheet_controller import install_css


class Window:
    _closed = False

    def __init__(self):
        self.classes = set()

    def add_css_class(self, name):
        self.classes.add(name)

    def remove_css_class(self, name):
        self.classes.discard(name)


class Manager:
    def __init__(self, dark=False):
        self.dark = dark
        self.callbacks = {}
        self.calls = []
        self._next_handler = 0

    def get_dark(self):
        return self.dark

    def connect(self, signal, callback):
        self.calls.append(signal)
        self._next_handler += 1
        self.callbacks[self._next_handler] = callback
        return self._next_handler

    def disconnect(self, handler):
        del self.callbacks[handler]

    def change(self, dark):
        self.dark = dark
        for callback in tuple(self.callbacks.values()):
            callback(self, None)


def fixture(*, dark=False, display=True):
    manager = Manager(dark=dark)
    providers = []
    registrations = []

    class Provider:
        def load_from_data(self, css):
            self.css = css
            providers.append(self)

    def remove_provider_for_display(display, provider):
        registrations[:] = [
            item for item in registrations
            if not (item[0] is display and item[1] is provider)
        ]

    gtk = NS(
        CssProvider=Provider,
        STYLE_PROVIDER_PRIORITY_APPLICATION=600,
        StyleContext=NS(
            add_provider_for_display=lambda *args: registrations.append(args),
            remove_provider_for_display=remove_provider_for_display,
        ),
    )
    screen = object() if display else None
    gdk = NS(Display=NS(get_default=lambda: screen))
    adw = NS(StyleManager=NS(get_default=lambda: manager))
    return Window(), manager, gtk, gdk, adw, providers, registrations


def test_light_mode_uses_one_additive_scoped_overlay_for_all_core_and_plugin_pages():
    window, manager, gtk, gdk, adw, providers, registrations = fixture()
    install_css(window, Gtk=gtk, Gdk=gdk, Adw=adw)
    assert len(providers) == 2  # existing Core UI + selected V2 overlay
    assert providers[-1].css == MODERN_LIGHT_CSS
    assert len(registrations) == 2
    assert registrations[0][2] == gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    assert registrations[1][2] == gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
    assert MODERN_LIGHT_CLASS in window.classes
    assert manager.calls == ["notify::dark"]
    install_css(window, Gtk=gtk, Gdk=gdk, Adw=adw)
    assert len(providers) == 3  # legacy Core installer keeps its contract
    assert manager.calls == ["notify::dark"]
    assert registrations[-1][1] is providers[-1]  # base CSS still reinstalls
    assert registrations[-1][2] < registrations[1][2]  # overlay still wins
    assert sum(item.css == MODERN_LIGHT_CSS for item in providers) == 1


def test_dark_mode_never_receives_the_v2_light_palette_even_when_changed_live():
    window, manager, gtk, gdk, adw, providers, registrations = fixture(dark=True)
    install_css(window, Gtk=gtk, Gdk=gdk, Adw=adw)
    assert MODERN_LIGHT_CLASS not in window.classes
    manager.change(False)
    assert MODERN_LIGHT_CLASS in window.classes
    manager.change(True)
    assert MODERN_LIGHT_CLASS not in window.classes
    manager.change(False)
    assert MODERN_LIGHT_CLASS in window.classes
    assert len(manager.callbacks) == 1


def test_closed_window_and_unavailable_display_do_not_force_a_theme():
    window, manager, gtk, gdk, adw, providers, registrations = fixture(display=False)
    install_modern_light(window, Gtk=gtk, Gdk=gdk, Adw=adw)
    assert not providers and not registrations and not window.classes
    window, manager, gtk, gdk, adw, providers, registrations = fixture()
    window._closed = True
    install_css(window, Gtk=gtk, Gdk=gdk, Adw=adw)
    assert providers == [] and manager.calls == [] and window.classes == set()


@pytest.mark.parametrize("value", [1, "false", None, object()])
def test_non_boolean_dark_state_is_not_mistaken_for_light(value):
    manager = NS(get_dark=lambda: value)
    assert not modern_light_enabled(manager)
    window = Window()
    update_light_class(window, manager)
    assert MODERN_LIGHT_CLASS not in window.classes


def test_exceptional_appearance_fails_closed():
    def fail():
        raise RuntimeError("private-path /home/SECRET")
    assert not modern_light_enabled(NS(get_dark=fail))
    assert not modern_light_enabled(NS())
    window = Window()
    update_light_class(window, NS(get_dark=fail))
    assert not window.classes


def test_all_v2_visual_selectors_are_window_scoped_without_global_token_overrides():
    css = MODERN_LIGHT_CSS.decode("utf-8")
    import re
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selectors = []
    for selector in text.split("{")[:-1]:
        selectors.append(selector.rsplit("}", 1)[-1].strip())
    for selector in selectors:
        if selector.startswith("@define-color"):
            assert selector.split()[1].startswith("cifereca_light_")
        else:
            assert selector.startswith("window.cifereca-modern-light"), selector
    for surface in (
        ".sidebar", ".book-row", ".login-card", ".sync-source-panel",
        ".book-tag", ".detail-title", ".page-title", ".facet-value",
        ".sidebar row:selected .dim-label", ".sidebar row:checked .dim-label",
        "button.suggested-action", "button:focus-visible",
        "switch:checked", "progressbar progress", "scale highlight",
    ):
        assert surface in css
    assert "button.destructive-action" not in css
    assert "@define-color accent_" not in css
    assert "gtk-application-prefer-dark-theme" not in css
    assert "font-family" not in css  # preserve user's accessibility fonts


def test_original_prototype_is_adapted_not_reapplied_to_every_gtk_window():
    css = MODERN_LIGHT_CSS.decode()
    assert css.count("window.cifereca-modern-light") >= 12
    assert "color: white;" in css  # selected sidebar & primary button contrast
    assert "#1d5fcb" in css and "#f7faff" in css
    assert "window, window.background, .background" not in css
    assert "button, .button" not in css


def _relative_luminance(rgb):
    channels = [int(rgb[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= 0.04045
              else ((channel + 0.055) / 1.055) ** 2.4
              for channel in channels]
    return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))


def _contrast(left, right):
    a, b = sorted((_relative_luminance(left), _relative_luminance(right)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize(("ink", "surface"), [
    ("#ffffff", "#1d5fcb"),  # selected navigation and primary actions
    ("#152641", "#f7faff"),  # body and title text
    ("#4e6482", "#f7faff"),  # muted informational labels
    ("#12315f", "#e5eefc"),  # documentary badges
])
def test_v2_text_palette_meets_wcag_aa_normal_text_ratio(ink, surface):
    assert _contrast(ink, surface) >= 4.5


def test_selected_sidebar_secondary_labels_maintain_wcag_aa_contrast():
    css = MODERN_LIGHT_CSS.decode("utf-8")
    assert ("window.cifereca-modern-light .sidebar row:selected .dim-label,\n"
            "window.cifereca-modern-light .sidebar row:checked .dim-label {\n"
            "  color: white;") in css
    assert _contrast("#4e6482", "#1d5fcb") < 4.5
    assert _contrast("#ffffff", "#1d5fcb") >= 4.5


def test_theme_release_is_idempotent_and_unhooks_appearance_updates():
    window, manager, gtk, gdk, adw, providers, registrations = fixture()
    install_modern_light(window, Gtk=gtk, Gdk=gdk, Adw=adw)
    assert len(registrations) == 1 and len(manager.callbacks) == 1
    assert MODERN_LIGHT_CLASS in window.classes

    release_modern_light(window)
    release_modern_light(window)  # GTK can request teardown more than once.
    assert not registrations
    assert not manager.callbacks
    assert MODERN_LIGHT_CLASS not in window.classes
    assert window._modern_light_css_installed is False
    manager.change(False)
    assert MODERN_LIGHT_CLASS not in window.classes

    # A new live owner may install exactly one fresh provider after release.
    install_modern_light(window, Gtk=gtk, Gdk=gdk, Adw=adw)
    assert len(registrations) == 1 and len(manager.callbacks) == 1


def test_closing_one_of_two_windows_keeps_the_other_theme_live():
    first, manager, gtk, gdk, adw, providers, registrations = fixture()
    second = Window()
    install_modern_light(first, Gtk=gtk, Gdk=gdk, Adw=adw)
    install_modern_light(second, Gtk=gtk, Gdk=gdk, Adw=adw)
    assert len(registrations) == 2 and len(manager.callbacks) == 2

    release_modern_light(first)
    assert len(registrations) == 1 and len(manager.callbacks) == 1
    assert MODERN_LIGHT_CLASS not in first.classes
    assert MODERN_LIGHT_CLASS in second.classes

    manager.change(True)
    assert MODERN_LIGHT_CLASS not in second.classes
    manager.change(False)
    assert MODERN_LIGHT_CLASS in second.classes
    release_modern_light(second)
    assert not registrations and not manager.callbacks


def test_stylemanager_connection_failure_cannot_leak_a_global_css_provider():
    window, manager, gtk, gdk, adw, providers, registrations = fixture()

    def broken_connect(*_args):
        raise RuntimeError("no subscriber available")

    manager.connect = broken_connect
    install_modern_light(window, Gtk=gtk, Gdk=gdk, Adw=adw)
    assert not registrations
    assert not manager.callbacks
    assert MODERN_LIGHT_CLASS not in window.classes
    assert not getattr(window, "_modern_light_css_installed", False)
