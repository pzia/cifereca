"""P0 #1129: clickable bibliographic facets share one explicit filter action."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.facet_value_controller import (
    facet_value_button, facet_value_group, search_facet,
)
from cifereca.gui.query_model import QueryState


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "facet_value_controller.py"


class Button:
    def __init__(self):
        self.css = []
        self.handlers = {}
        self.tooltip = None
        self.accessible = []

    def add_css_class(self, value):
        self.css.append(value)

    def set_child(self, widget):
        self.child = widget

    def set_tooltip_text(self, value):
        self.tooltip = value

    def update_property(self, keys, values):
        self.accessible.append((keys, values))

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def click(self):
        self.handlers["clicked"](self)


class Label:
    def __init__(self, **kwargs):
        self.props = kwargs

    def set_ellipsize(self, mode):
        self.props["ellipsize"] = mode

    def set_max_width_chars(self, count):
        self.props["max_width_chars"] = count


class FlowBox:
    def __init__(self):
        self.props = {}
        self.children = []

    def set_selection_mode(self, value):
        self.props["selection_mode"] = value

    def set_halign(self, value):
        self.props["halign"] = value

    def set_valign(self, value):
        self.props["valign"] = value

    def set_min_children_per_line(self, value):
        self.props["minimum"] = value

    def set_max_children_per_line(self, value):
        self.props["maximum"] = value

    def set_column_spacing(self, value):
        self.props["column_spacing"] = value

    def set_row_spacing(self, value):
        self.props["row_spacing"] = value

    def insert(self, item, position):
        assert position == -1
        self.children.append(item)


Gtk = NS(
    Button=Button, Label=Label, FlowBox=FlowBox,
    AccessibleProperty=NS(LABEL="accessible-label"),
    SelectionMode=NS(NONE="none"),
    Align=NS(START="start", CENTER="center"),
)
Pango = NS(EllipsizeMode=NS(END="end"))


class Controls:
    def __init__(self, initial=None):
        self.current = initial if initial is not None else QueryState()
        self.calls = []

    def state(self):
        return self.current

    def set_state(self, value, *, notify=True):
        self.calls.append((value, notify))
        self.current = value


def _host():
    events = []
    library, search = Controls(), Controls()

    window = NS(
        _closed=False, library_controls=library, search_controls=search,
        navigate=lambda route: events.append(("navigate", route)),
        _render_library=lambda: events.append(("render-library",)),
        run_search=lambda force: events.append(("search", force)),
    )
    window._search_facet = lambda field, value, controls: search_facet(
        window, field, value, controls,
    )
    window._facet_value_button = lambda label, field, value, controls: facet_value_button(
        window, label, field, value, controls, Gtk=Gtk, Pango=Pango,
    )
    return window, library, search, events


def test_module_is_headless_with_stable_three_method_mainwindow_facades():
    source = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(source)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in entry or "main_window" in entry for entry in imports)
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    for facade, target, arguments in (
        ("_search_facet", "search_facet", ["self", "field", "text", "controls"]),
        ("_facet_value_group", "facet_value_group", ["self", "choices", "field", "controls"]),
        ("_facet_value_button", "facet_value_button",
         ["self", "label", "field", "value", "controls"]),
    ):
        method = next(node for node in owner.body
                      if isinstance(node, ast.FunctionDef) and node.name == facade)
        assert [arg.arg for arg in method.args.args] == arguments
        assert f"facet_value_controller.{target}" in ast.unparse(method)


def test_library_facet_sets_state_silently_then_renders_library_once():
    window, library, search, events = _host()
    search_facet(window, "language", "fr-FR", library)
    assert library.current.languages == ("fr",)
    assert len(library.calls) == 1
    assert library.calls[0][1] is False
    assert search.calls == []
    assert events == [("navigate", "library"), ("render-library",)]


def test_search_facet_sets_only_search_controls_silently_and_searches_once():
    window, library, search, events = _host()
    search_facet(window, "author", "Auteur : Harari, Yuval Noah", search)
    assert search.current.text == "Harari Yuval Noah"
    assert search.current.field == "author"
    assert search.calls[0][1] is False
    assert library.calls == []
    assert events == [("navigate", "search"), ("search", False)]


def test_existing_facet_still_triggers_explicit_single_refresh():
    window, library, search, events = _host()
    search.current = QueryState(text="Harari Yuval Noah", field="author")
    search_facet(window, "author", "Harari Yuval Noah", search)
    assert search.current.text == "Harari Yuval Noah"
    assert len(search.calls) == 1
    assert events == [("navigate", "search"), ("search", False)]


def test_foreign_controls_do_not_trigger_library_context_or_update_library():
    window, library, search, events = _host()
    foreign = Controls(QueryState(text="roman", languages=("en",)))
    search_facet(window, "language", "fr", foreign)
    assert search.current.text == "roman"
    assert search.current.languages == ("fr",)
    assert foreign.calls == []
    assert library.calls == []
    assert events == [("navigate", "search"), ("search", False)]


def test_closed_window_does_not_read_controls_or_navigate():
    window = NS(_closed=True)
    search_facet(window, "author", "Private", object())


def test_widget_is_native_clickable_value_with_full_accessible_name():
    window, library, search, events = _host()
    caption = "Auteur avec un nom bibliographique extrêmement long"
    button = facet_value_button(
        window, caption, "author", "nom long", search, Gtk=Gtk, Pango=Pango,
    )
    assert button.css == ["flat", "facet-value"]
    assert button.child.props == {
        "label": caption, "xalign": 0,
        "ellipsize": "end", "max_width_chars": 28,
    }
    assert button.tooltip == "Filtrer par " + caption
    assert button.accessible == [
        (["accessible-label"], ["Filtrer par " + caption]),
    ]
    assert button._cifereca_facet_field == "author"
    assert button._cifereca_facet_value == "nom long"
    assert search.calls == []
    button.click()
    assert search.current.text == "nom long"
    assert events == [("navigate", "search"), ("search", False)]


def test_facet_value_metadata_is_preserved_when_label_differs():
    window, library, search, events = _host()
    button = facet_value_button(
        window, "French", "language", "fr-FR", search, Gtk=Gtk, Pango=Pango,
    )
    button.click()
    assert search.current.languages == ("fr",)
    assert button.child.props["label"] == "French"
    assert button._cifereca_facet_value == "fr-FR"


def test_empty_label_falls_back_to_value_without_markup():
    window, library, search, events = _host()
    button = facet_value_button(
        window, None, "subject", "<b>Archives</b>", search, Gtk=Gtk, Pango=Pango,
    )
    assert button.child.props["label"] == "<b>Archives</b>"
    assert button.tooltip == "Filtrer par <b>Archives</b>"
    assert not hasattr(button.child, "markup")


@pytest.mark.parametrize("failure", [AttributeError, TypeError])
def test_optional_gtk_accessibility_fallback_keeps_real_button_usable(failure):
    window, library, search, events = _host()
    class FailingAccessibleButton(Button):
        def update_property(self, keys, values):
            raise failure("unsupported Gtk property")
    gtk = NS(**{**Gtk.__dict__, "Button": FailingAccessibleButton})
    button = facet_value_button(
        window, "A", "author", "A", search, Gtk=gtk, Pango=Pango,
    )
    button.click()
    assert events == [("navigate", "search"), ("search", False)]


def test_group_orders_values_and_binds_exact_owning_controls():
    window, library, search, events = _host()
    group = facet_value_group(
        window, [("B", "bob"), ("A", "alice")],
        "author", library, Gtk=Gtk,
    )
    assert [button.child.props["label"] for button in group.children] == ["B", "A"]
    group.children[1].click()
    assert library.current.field == "author"
    assert library.current.text == "alice"
    assert search.calls == []
    assert events == [("navigate", "library"), ("render-library",)]


def test_group_removes_casefold_duplicate_only_when_value_also_equal():
    window, library, search, events = _host()
    group = facet_value_group(
        window,
        [("  ÉTÉ  ", "summer"), ("été", "summer"),
         ("Été", "other"), ("", "empty"), ("  ", "blank")],
        "subject", search, Gtk=Gtk,
    )
    assert len(group.children) == 2
    assert [b._cifereca_facet_value for b in group.children] == ["summer", "other"]


def test_group_is_bounded_and_nonselecting():
    window, library, search, events = _host()
    group = facet_value_group(
        window, [(f"label-{i}", str(i)) for i in range(8)],
        "subject", search, Gtk=Gtk,
    )
    assert len(group.children) == 8
    assert group.props == {
        "selection_mode": "none", "halign": "start", "valign": "center",
        "minimum": 1, "maximum": 4,
        "column_spacing": 4, "row_spacing": 4,
    }


def test_group_works_when_all_values_are_empty():
    window, library, search, events = _host()
    group = facet_value_group(
        window, [("", "id"), (" ", "other")], "author", search, Gtk=Gtk,
    )
    assert group.children == []
    assert events == []


def test_queued_click_after_close_does_not_modify_filters_or_navigate():
    window, library, search, events = _host()
    button = facet_value_button(
        window, "En", "language", "en", search, Gtk=Gtk, Pango=Pango,
    )
    window._closed = True
    button.click()
    assert library.calls == [] and search.calls == []
    assert events == []
