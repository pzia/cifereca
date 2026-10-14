"""P0 #1051: dashboard page GTK layout and explicit synchronization actions."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.dashboard_page_controller import build_dashboard_page


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "dashboard_page_controller.py"


class Widget:
    def __init__(self, **options):
        self.options = options
        self.props = {}
        self.children = []
        self.signals = {}

    def append(self, widget):
        self.children.append(widget)

    def set_child(self, widget):
        self.child = widget

    def connect(self, name, handler):
        self.signals[name] = handler

    def click(self):
        self.signals["clicked"](self)

    def __getattr__(self, name):
        if name.startswith("set_"):
            return lambda *args: self.props.__setitem__(
                name[4:], args[0] if len(args) == 1 else args
            )
        raise AttributeError(name)


class ActionBar(Widget):
    def __init__(self, *, source):
        super().__init__()
        self.source = source


GTK = NS(
    ScrolledWindow=Widget, Box=Widget, Label=Widget, FlowBox=Widget,
    PolicyType=NS(NEVER="never", AUTOMATIC="auto"),
    Orientation=NS(VERTICAL="vertical", HORIZONTAL="horizontal"),
    Align=NS(END="end"),
    SelectionMode=NS(NONE="none"),
)


def _fixture():
    events, styled = [], []
    def make_action(title, icon_name, **kwargs):
        action = Widget(title=title, icon_name=icon_name, **kwargs)
        events.append(("constructed-action", title))
        return action
    window = NS(
        _closed=False,
        action_button=make_action,
        run_opening_sync=lambda button, *, deep=False:
            events.append(("sync", button, deep)),
    )
    def css(widget, *classes):
        styled.append((widget, classes))
    return window, events, styled, css


def _render():
    window, events, styled, css = _fixture()
    scroll = build_dashboard_page(
        window, Gtk=GTK, add_css=css, AdaptiveActionBar=ActionBar,
    )
    outer = scroll.child
    heading, subtitle, status, cards = outer.children
    title, actions = heading.children
    refresh, deep = actions.children
    return NS(
        window=window, events=events, styled=styled, scroll=scroll, outer=outer,
        heading=heading, subtitle=subtitle, status=status, cards=cards,
        title=title, actions=actions, refresh=refresh, deep=deep,
    )


def test_builder_is_headless_and_preserves_public_mainwindow_facade():
    owner = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(owner)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in entry or "main_window" in entry for entry in imports)
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "MainWindow")
    facade = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_build_dashboard_page")
    assert [a.arg for a in facade.args.args] == ["self"]
    source = ast.unparse(facade)
    assert "dashboard_page_controller.build_dashboard_page" in source
    assert "AdaptiveActionBar=AdaptiveActionBar" in source
    assert "Gtk=Gtk" in source and "add_css=add_css" in source


def test_closed_parent_skips_widget_construction_without_dependencies():
    window = NS(_closed=True)
    assert build_dashboard_page(window, Gtk=None, add_css=None, AdaptiveActionBar=None) is None


def test_page_is_scrollable_with_unchanged_policy_and_margins():
    ui = _render()
    assert ui.scroll.props["policy"] == ("never", "auto")
    assert ui.outer.options == {"orientation": "vertical", "spacing": 14}
    for side in ("margin_top", "margin_bottom", "margin_start", "margin_end"):
        assert ui.outer.props[side] == 24
    assert ui.scroll.child is ui.outer


def test_page_heading_and_subtitle_keep_french_labels_and_layout():
    ui = _render()
    assert ui.heading.options == {"orientation": "horizontal", "spacing": 8}
    assert ui.title.options["label"] == "Bibliothèque familiale"
    assert ui.title.props["hexpand"] is True
    assert ui.title.props["wrap"] is True
    assert ui.subtitle.options["label"] == (
        "État des identités et de la synchronisation automatique"
    )
    assert (ui.title, ("page-title",)) in ui.styled
    assert (ui.subtitle, ("dim-label",)) in ui.styled


def test_background_connection_state_is_available_before_network_start():
    ui = _render()
    assert ui.window.dashboard_loading_status is ui.status
    assert ui.status.options["label"] == "Connexion aux sources en arrière-plan…"
    assert ui.status.props["wrap"] is True
    assert (ui.status, ("dim-label", "caption")) in ui.styled
    assert not any(event[0] == "sync" for event in ui.events)


def test_two_buttons_are_in_one_adaptive_action_bar_at_right_of_heading():
    ui = _render()
    assert ui.actions.source is ui.heading
    assert ui.heading.children == [ui.title, ui.actions]
    assert ui.actions.props["halign"] == "end"
    assert ui.actions.children == [ui.refresh, ui.deep]
    assert ui.refresh.options == {
        "title": "Synchronisation rapide",
        "icon_name": "view-refresh-symbolic",
        "suggested": True,
    }
    assert ui.deep.options == {
        "title": "Vérifier toute la bibliothèque",
        "icon_name": "system-search-symbolic",
    }


def test_quick_sync_is_strictly_click_triggered_with_deep_false():
    ui = _render()
    assert ui.events == [
        ("constructed-action", "Synchronisation rapide"),
        ("constructed-action", "Vérifier toute la bibliothèque"),
    ]
    ui.refresh.click()
    assert ui.events[-1] == ("sync", ui.refresh, False)
    assert len([event for event in ui.events if event[0] == "sync"]) == 1


def test_deep_sync_is_strictly_click_triggered_with_deep_true():
    ui = _render()
    ui.deep.click()
    assert ui.events[-1] == ("sync", ui.deep, True)
    assert len([event for event in ui.events if event[0] == "sync"]) == 1


def test_separate_actions_never_capture_one_anothers_button():
    ui = _render()
    ui.deep.click()
    ui.refresh.click()
    assert ui.events[-2:] == [
        ("sync", ui.deep, True),
        ("sync", ui.refresh, False),
    ]


def test_card_flow_has_three_columns_and_non_selectable_grid():
    ui = _render()
    assert ui.window.dashboard_cards is ui.cards
    assert ui.cards.props["selection_mode"] == "none"
    assert ui.cards.props["min_children_per_line"] == 1
    assert ui.cards.props["max_children_per_line"] == 3
    assert ui.cards.props["homogeneous"] is True
    assert ui.cards.props["column_spacing"] == 12
    assert ui.cards.props["row_spacing"] == 12
    assert ui.outer.children[-1] is ui.cards
    assert ui.cards.children == []


def test_composition_does_not_access_plugin_or_service_or_start_scan():
    window, events, styled, css = _fixture()
    build_dashboard_page(window, Gtk=GTK, add_css=css, AdaptiveActionBar=ActionBar)
    assert not hasattr(window, "plugin_manager")
    assert not hasattr(window, "service")
    assert all(event[0] == "constructed-action" for event in events)


def test_each_window_receives_new_widgets_and_distinct_callbacks():
    a, b = _render(), _render()
    assert a.scroll is not b.scroll
    assert a.cards is not b.cards
    assert a.refresh is not b.refresh
    a.refresh.click()
    assert not any(event[0] == "sync" for event in b.events)
    assert a.events[-1] == ("sync", a.refresh, False)


def test_labels_are_not_interpolated_from_private_identity_data():
    ui = _render()
    labels = [
        ui.title.options["label"], ui.subtitle.options["label"],
        ui.status.options["label"],
    ]
    assert all("@" not in label for label in labels)
