"""P0 #1051: GTK-free source for shell stylesheet with exact CSS contracts."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.shell_stylesheet_controller import install_css


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "shell_stylesheet_controller.py"


def _fixture(*, display=True):
    events = []
    providers = []

    class CssProvider:
        def __init__(self):
            providers.append(self)
            events.append(("create",))

        def load_from_data(self, stylesheet):
            self.stylesheet = stylesheet
            events.append(("load", stylesheet))

    screen = object() if display else None
    gtk = NS(
        CssProvider=CssProvider,
        STYLE_PROVIDER_PRIORITY_APPLICATION=600,
        StyleContext=NS(add_provider_for_display=lambda screen, provider, priority:
                        events.append(("register", screen, provider, priority))),
    )
    gdk = NS(Display=NS(get_default=lambda: (
        events.append(("display",)) or screen
    )))
    window = NS(_closed=False)
    return window, gtk, gdk, screen, events, providers


def _run():
    window, gtk, gdk, screen, events, providers = _fixture()
    install_css(window, Gtk=gtk, Gdk=gdk)
    return window, screen, events, providers


def _css():
    _window, _screen, _events, providers = _run()
    return providers[0].stylesheet.decode("utf-8")


def test_owner_without_gtk_import_and_mainwindow_facade():
    module = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(module)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in row or "main_window" in row for row in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in main.body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_install_css")
    assert [arg.arg for arg in method.args.args] == ["self"]
    text = ast.unparse(method)
    assert "shell_stylesheet_controller.install_css" in text
    assert "Gtk=Gtk" in text and "Gdk=Gdk" in text


def test_application_priority_registers_one_provider_on_current_display():
    window, screen, events, providers = _run()
    assert len(providers) == 1
    assert [event[0] for event in events] == [
        "create", "load", "display", "register",
    ]
    assert events[-1] == ("register", screen, providers[0], 600)
    assert isinstance(providers[0].stylesheet, bytes)


def test_css_without_display_is_loaded_but_not_registered():
    window, gtk, gdk, screen, events, providers = _fixture(display=False)
    install_css(window, Gtk=gtk, Gdk=gdk)
    assert len(providers) == 1
    assert providers[0].stylesheet.startswith(b"\n")
    assert not any(e[0] == "register" for e in events)


def test_closed_window_skips_css_provider_and_display_access():
    window, gtk, gdk, screen, events, providers = _fixture()
    window._closed = True
    install_css(window, Gtk=gtk, Gdk=gdk)
    assert events == []
    assert providers == []


def test_sidebar_and_navigation_focus_selectors_remain_present():
    css = _css()
    assert ".sidebar" in css
    assert ".sidebar row" in css
    assert ".sidebar row:selected" in css
    assert "@sidebar_bg_color" in css
    assert "@accent_bg_color" in css


def test_book_row_typography_and_cover_style_are_preserved():
    css = _css()
    for selector in (
        ".book-row", ".book-row:hover", ".book-title", ".page-title",
        ".detail-title", ".detail-subtitle", ".cover-frame",
    ):
        assert selector in css
    assert "font-weight: 800;" in css


def test_book_facet_tags_are_keyboard_accessible_with_focus_and_hover_styles():
    css = _css()
    for selector in (
        ".book-tag:focus", ".facet-value", ".facet-value:hover",
        ".facet-value:focus", ".tag-content", ".tag-form",
    ):
        assert selector in css
    assert "outline: 2px solid @accent_color;" in css
    assert "background: alpha(@accent_color, 0.16);" in css


def test_feedback_and_notification_bars_remain_distinct():
    css = _css()
    assert ".busy-bar" in css
    assert ".notification-bar" in css
    assert "border-top: 1px solid @borders;" in css
    assert ".activity-success" in css
    assert ".activity-error" in css


def test_loan_due_urgency_colors_follow_named_theme_tokens():
    css = _css()
    assert ".loan-overdue" in css and "@error_color" in css
    assert ".loan-imminent" in css and "@warning_color" in css
    assert ".loan-normal { opacity: 0.85; }" in css


def test_settings_and_branding_classes_keep_spacing_and_emphasis():
    css = _css()
    for selector in (".login-card", ".settings-tabs button",
                     ".metric", ".login-brand", ".badge", ".success-badge"):
        assert selector in css
    assert "font-size: 2.1em;" in css
    assert "min-width: 0;" in css


def test_css_uses_theme_tokens_instead_of_hardcoded_hex_colors():
    css = _css()
    for token in (
        "@card_bg_color", "@sidebar_bg_color", "@borders",
        "@accent_bg_color", "@accent_fg_color", "@success_color",
        "@error_color", "@warning_color",
    ):
        assert token in css
    assert "background: #" not in css
    assert "color: #" not in css


def test_calling_install_twice_keeps_same_stylesheet_no_global_cache():
    window, gtk, gdk, screen, events, providers = _fixture()
    install_css(window, Gtk=gtk, Gdk=gdk)
    install_css(window, Gtk=gtk, Gdk=gdk)
    assert len(providers) == 2
    assert providers[0].stylesheet == providers[1].stylesheet
    assert len([e for e in events if e[0] == "register"]) == 2
