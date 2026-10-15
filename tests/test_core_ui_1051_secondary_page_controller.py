"""P0 #1051: GTK-free History and Bookings page composition contracts."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.secondary_page_controller import (
    build_history_page, build_bookings_page,
)
from cifereca.language import language_label


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "secondary_page_controller.py"


class Widget:
    def __init__(self):
        self.children = []
        self.handlers = {}
        self.tooltip = None

    def append(self, child):
        self.children.append(child)

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def set_tooltip_text(self, tooltip):
        self.tooltip = tooltip

    def click(self):
        self.handlers["clicked"](self)


class Button(Widget):
    @classmethod
    def new_from_icon_name(cls, icon):
        obj = cls()
        obj.icon = icon
        return obj


class Adjustment:
    def __init__(self):
        self.handlers = {}

    def connect(self, event, callback):
        self.handlers[event] = callback


class Page:
    def __init__(self, window, title, subtitle, empty_title, empty_description):
        self.window = window
        self.args = (title, subtitle, empty_title, empty_description)
        self.header_actions = Widget()
        self.toolbar = Widget()
        self.scroll = NS(get_vadjustment=lambda: self.adjustment)
        self.adjustment = Adjustment()
        self.total_only = None
        self.on_selected = None

    def set_total_only_count(self, value):
        self.total_only = value

    def connect_selected(self, callback):
        self.on_selected = callback


class MultiSelect:
    def __init__(self, title, choices, callback):
        self.title = title
        self.choices = choices
        self.callback = callback


Gtk = NS(Button=Button)


def _window():
    calls = []
    window = NS(
        _closed=False,
        refresh_history=lambda force, control: calls.append(("history", force, control)),
        refresh_bookings=lambda force, control: calls.append(("bookings", force, control)),
        _rerender_history=lambda: calls.append(("languages",)),
        _history_selected=lambda row: calls.append(("selected_history", row)),
        _booking_selected=lambda row: calls.append(("selected_booking", row)),
        _history_scroll_changed=lambda adjustment: calls.append(("scroll", adjustment)),
    )
    return window, calls


def _history(window):
    return build_history_page(
        window, Gtk=Gtk, MasterDetailPage=Page, MultiSelect=MultiSelect,
    )


def _bookings(window):
    return build_bookings_page(
        window, Gtk=Gtk, MasterDetailPage=Page,
    )


def test_imports_are_headless_and_mainwindow_facades_preserve_signatures():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in item or "main_window" in item for item in imports)
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    for facade, target in (
        ("_build_history_page", "build_history_page"),
        ("_build_bookings_page", "build_bookings_page"),
    ):
        method = next(node for node in cls.body
                      if isinstance(node, ast.FunctionDef) and node.name == facade)
        assert [arg.arg for arg in method.args.args] == ["self"]
        assert f"secondary_page_controller.{target}" in ast.unparse(method)


def test_history_page_retains_business_total_mode_and_original_descriptions():
    window, calls = _window()
    page = _history(window)
    assert page.window is window
    assert page.args == (
        "Historique", "Documents précédemment empruntés",
        "Historique non chargé",
        "Ouvrez cet écran ou actualisez pour récupérer l'historique.",
    )
    assert page.total_only is True
    assert len(page.header_actions.children) == 1
    assert len(page.toolbar.children) == 1
    assert calls == []


def test_bookings_page_does_not_force_progressive_business_total_count():
    window, calls = _window()
    page = _bookings(window)
    assert page.window is window
    assert page.args == (
        "Réservations", "Réservations physiques et numériques",
        "Aucune réservation", "Vos réservations apparaîtront ici.",
    )
    assert page.total_only is None
    assert len(page.header_actions.children) == 1
    assert page.toolbar.children == []
    assert calls == []


def test_history_refresh_is_explicit_and_reuses_exact_clicked_control():
    window, calls = _window()
    page = _history(window)
    refresh = page.header_actions.children[0]
    assert refresh.icon == "view-refresh-symbolic"
    assert refresh.tooltip == "Actualiser l'historique"
    refresh.click()
    assert calls == [("history", True, refresh)]


def test_bookings_refresh_is_explicit_and_reuses_exact_clicked_control():
    window, calls = _window()
    page = _bookings(window)
    refresh = page.header_actions.children[0]
    assert refresh.icon == "view-refresh-symbolic"
    assert refresh.tooltip == "Actualiser les réservations"
    refresh.click()
    assert calls == [("bookings", True, refresh)]


def test_no_refresh_is_implicitly_submitted_when_page_constructed():
    window, calls = _window()
    _history(window)
    _bookings(window)
    assert calls == []
    assert not hasattr(window, "runner")
    assert not hasattr(window, "service")


def test_history_filter_languages_keep_original_order_and_current_rerender_handler():
    window, calls = _window()
    page = _history(window)
    filt = window.history_languages
    assert page.toolbar.children == [filt]
    assert filt.title == "Langues"
    codes = ("fr", "en", "de", "es", "it", "pt", "nl")
    assert filt.choices == [(code, language_label(code)) for code in codes]
    filt.callback()
    assert calls == [("languages",)]


def test_history_selection_forwards_exact_item_identity():
    window, calls = _window()
    page = _history(window)
    row = object()
    page.on_selected(row)
    assert calls == [("selected_history", row)]


def test_bookings_selection_forwards_exact_item_identity():
    window, calls = _window()
    page = _bookings(window)
    row = object()
    page.on_selected(row)
    assert calls == [("selected_booking", row)]


def test_history_scroll_observes_only_its_own_adjustment():
    window, calls = _window()
    page = _history(window)
    assert list(page.adjustment.handlers) == ["value-changed"]
    page.adjustment.handlers["value-changed"](page.adjustment)
    assert calls == [("scroll", page.adjustment)]


def test_bookings_does_not_attach_unnecessary_history_scroll_or_filter():
    window, calls = _window()
    page = _bookings(window)
    assert page.adjustment.handlers == {}
    assert not hasattr(window, "history_languages")


@pytest.mark.parametrize("builder", [_history, _bookings])
def test_closed_window_does_not_construct_or_touch_widgets(builder):
    builder(NS(_closed=True))


@pytest.mark.parametrize("builder,event_name", [
    (_history, "history"),
    (_bookings, "bookings"),
])
def test_late_refresh_click_after_parent_closed_does_not_queue_private_work(builder, event_name):
    window, calls = _window()
    page = builder(window)
    window._closed = True
    page.header_actions.children[0].click()
    assert not any(item[0] == event_name for item in calls)


def test_two_pages_use_separate_refresh_buttons_and_listeners():
    window, calls = _window()
    history = _history(window)
    bookings = _bookings(window)
    assert history.header_actions.children[0] is not bookings.header_actions.children[0]
    history.header_actions.children[0].click()
    bookings.header_actions.children[0].click()
    assert [event[0] for event in calls] == ["history", "bookings"]
