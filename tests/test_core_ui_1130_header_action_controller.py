"""P0 #1130: header menu and contextual actions dispatched via the real adapter."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.header_action_controller import refresh_header_actions
from cifereca.gui.shell_model import (
    menu_sections, validate_action_selection, visible_actions,
)
from cifereca.plugin_api import UiPageContribution, UiActionContribution


OWNER = Path(__file__).resolve().parents[1] / "src/cifereca/gui/header_action_controller.py"
MAIN = OWNER.with_name("main_window.py")


class Box:
    def __init__(self):
        self.children = []

    def append(self, item):
        self.children.append(item)


class Button:
    def __init__(self, name):
        self.name = name
        self.tooltip = None
        self.callback = None

    @classmethod
    def new_from_icon_name(cls, name):
        return cls(name)

    def set_tooltip_text(self, title):
        self.tooltip = title

    def connect(self, _event, callback):
        self.callback = callback

    def click(self):
        self.callback(self)


class Action:
    def __init__(self, name):
        self.name = name
        self.callback = None

    @classmethod
    def new(cls, name, _params):
        return cls(name)

    def connect(self, _signal, callback):
        self.callback = callback

    def activate(self):
        self.callback(self, None)


class Group:
    def __init__(self):
        self.actions = {}

    def add_action(self, action):
        self.actions[action.name] = action


class Menu:
    def __init__(self):
        self.entries = []
        self.sections = []

    def append(self, title, action):
        self.entries.append((title, action))

    def append_section(self, title, section):
        self.sections.append((title, section))


GTK = NS(Button=Button)
GIO = NS(Menu=Menu, SimpleAction=Action, SimpleActionGroup=Group)


def _page(route, *, actions=(), order=20):
    return UiPageContribution(
        route=route, title=route.title(), order=order,
        factory=lambda _host: object(), actions=actions,
    )


def _action(name, *, global_action=False):
    return UiActionContribution(
        id=name, title=name.title(), icon_name="dialog-information-symbolic",
        global_action=global_action,
        callback=lambda host: host.events.append(name),
    )


def _window(selected=None):
    events = []
    diagnostics = []
    menu = NS(set_menu_model=lambda model: setattr(menu, "model", model))
    window = NS(
        events=events,
        navigate=lambda route: events.append(("navigate", route)),
        diagnostics=NS(add=lambda *args, **kwargs: diagnostics.append((args, kwargs))),
        _titlebar_service=NS(selected_actions=lambda: selected) if selected is not None else None,
        _pages={
            "search": _page("search", actions=(_action("online"),)),
            "library": _page("library", actions=(
                _action("import-acsm", global_action=True), _action("local"),
            ), order=30),
        },
        header_actions=Box(),
        shell_menu=menu,
        _UiHost=lambda owner: owner,
        _invoke_ui_action=lambda action, host: action.callback(host),
        insert_action_group=lambda prefix, group: events.append(("group", prefix, group)),
        _ensure_pages_menu_rightmost=lambda: events.append("rightmost"),
    )
    return window, diagnostics


def _refresh(window, route):
    return refresh_header_actions(
        window, route, Gtk=GTK, Gio=GIO,
        clear_box=lambda box: box.children.clear(),
        validate_action_selection=validate_action_selection,
        visible_actions=visible_actions,
        menu_sections=menu_sections,
    )


def test_module_remains_gtk_free_with_stable_mainwindow_facade():
    owner = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(owner)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(x == "import gi" or x.startswith("from gi ")
                   or "main_window" in x for x in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    facade = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_refresh_header_actions")
    assert [a.arg for a in facade.args.args] == ["self", "route"]
    assert "header_action_controller.refresh_header_actions" in ast.unparse(facade)


def test_unknown_titlebar_actions_warn_once_and_are_never_rendered():
    window, diagnostics = _window(["stale.action", "search.online", "stale.action"])
    _refresh(window, "search")
    assert len(diagnostics) == 1
    assert diagnostics[0][1]["detail"] == {"obsolete_action_ids": ["stale.action"]}
    assert len(window.header_actions.children) == 1
    window.header_actions.children[0].click()
    assert "online" in window.events
    _refresh(window, "search")
    assert len(diagnostics) == 1
    assert len(window.header_actions.children) == 1


def test_global_action_is_visible_from_other_route_but_local_one_is_not():
    window, diagnostics = _window(["library.local", "library.import-acsm", "search.online"])
    _refresh(window, "search")
    assert [button.tooltip for button in window.header_actions.children] == [
        "Online", "Import-Acsm",
    ]
    window.header_actions.children[1].click()
    assert "import-acsm" in window.events
    _refresh(window, "library")
    assert [button.tooltip for button in window.header_actions.children] == [
        "Import-Acsm", "Local",
    ]


def test_menu_replaces_action_group_and_callback_uses_current_host():
    window, diagnostics = _window()
    _refresh(window, "search")
    first = window._page_menu_actions
    assert "item0" in first.actions
    assert window.events[-1] == "rightmost"
    for title, section in window.shell_menu.model.sections:
        for name, command in section.entries:
            if name == "Library":
                first.actions[command.removeprefix("pagemenu.")].activate()
    assert ("navigate", "library") in window.events
    _refresh(window, "library")
    assert window._page_menu_actions is not first
    assert window.events[-1] == "rightmost"
    assert len(window.shell_menu.model.sections) >= 2
