"""#1051 / #4147: History page remains; bookings page has been retired."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui import secondary_page_controller
from cifereca.gui.secondary_page_controller import build_history_page
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
        button = cls()
        button.icon = icon
        return button


class Adjustment:
    def __init__(self):
        self.handlers = {}

    def connect(self, event, callback):
        self.handlers[event] = callback


class Page:
    def __init__(self, window, title, subtitle, empty_title, empty_description):
        self.window = window
        self.args = title, subtitle, empty_title, empty_description
        self.header_actions = Widget()
        self.toolbar = Widget()
        self.adjustment = Adjustment()
        self.scroll = NS(get_vadjustment=lambda: self.adjustment)
        self.total_only = None
        self.on_selected = None

    def set_total_only_count(self, value):
        self.total_only = value

    def connect_selected(self, callback):
        self.on_selected = callback


class MultiSelect:
    def __init__(self, title, choices, callback):
        self.title, self.choices, self.callback = title, choices, callback


def _window():
    calls = []
    window = NS(
        _closed=False,
        refresh_history=lambda force, control: calls.append(("history", force, control)),
        _rerender_history=lambda: calls.append(("languages",)),
        _history_selected=lambda row: calls.append(("selected_history", row)),
        _history_scroll_changed=lambda adjustment: calls.append(("scroll", adjustment)),
    )
    return window, calls


def _history(window):
    return build_history_page(
        window, Gtk=NS(Button=Button),
        MasterDetailPage=Page, MultiSelect=MultiSelect,
    )


def test_controller_remains_headless_and_only_history_facade_is_exposed():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in x or "main_window" in x for x in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body
               if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_build_history_page")
    assert [arg.arg for arg in method.args.args] == ["self"]
    assert "secondary_page_controller.build_history_page" in ast.unparse(method)
    assert not hasattr(secondary_page_controller, "build_bookings_page")
    assert not any(n.name == "_build_bookings_page" for n in cls.body
                   if isinstance(n, ast.FunctionDef))


def test_history_page_keeps_business_total_and_original_labels():
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


def test_history_refresh_only_from_explicit_click_on_exact_control():
    window, calls = _window()
    page = _history(window)
    button = page.header_actions.children[0]
    assert button.icon == "view-refresh-symbolic"
    assert button.tooltip == "Actualiser l'historique"
    assert calls == []
    button.click()
    assert calls == [("history", True, button)]


def test_history_filter_preserves_language_order_and_rerender_handler():
    window, calls = _window()
    page = _history(window)
    languages = window.history_languages
    assert page.toolbar.children == [languages]
    assert languages.title == "Langues"
    codes = ("fr", "en", "de", "es", "it", "pt", "nl")
    assert languages.choices == [(code, language_label(code)) for code in codes]
    languages.callback()
    assert calls == [("languages",)]


def test_history_selection_and_scroll_are_scoped_to_history_page():
    window, calls = _window()
    page = _history(window)
    selected = object()
    page.on_selected(selected)
    assert calls == [("selected_history", selected)]
    assert list(page.adjustment.handlers) == ["value-changed"]
    page.adjustment.handlers["value-changed"](page.adjustment)
    assert calls[-1] == ("scroll", page.adjustment)


def test_closed_window_never_constructs_or_refreshes_history():
    assert _history(NS(_closed=True)) is None
    window, calls = _window()
    page = _history(window)
    window._closed = True
    page.header_actions.children[0].click()
    assert calls == []
