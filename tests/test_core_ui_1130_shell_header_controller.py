"""CoreUI P0 #1130: actual shell header assembly with injected GTK fakes."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.shell_header_controller import create_shell_header


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "shell_header_controller.py"


class Widget:
    def __init__(self, **kwargs):
        self.props = kwargs
        self.children = []
        self.handlers = {}
        self.tooltip = None

    def append(self, child):
        self.children.append(child)

    def pack_start(self, child):
        self.children.append(child)

    def pack_end(self, child):
        self.end = child

    def set_title_widget(self, child):
        self.title = child

    def set_tooltip_text(self, tooltip):
        self.tooltip = tooltip

    def set_popover(self, child):
        self.popover = child

    def set_factory(self, factory):
        self.factory = factory

    def set_list_factory(self, factory):
        self.list_factory = factory

    def connect(self, signal, handler):
        self.handlers[signal] = handler

    def click(self):
        self.handlers["clicked"](self)

    def set_ellipsize(self, mode):
        self.props["ellipsize"] = mode

    def set_single_line_mode(self, enabled):
        self.props["single_line"] = enabled

    def set_max_width_chars(self, chars):
        self.props["max_width_chars"] = chars

    def set_label(self, value):
        self.props["label"] = value

    def set_can_shrink(self, value):
        self.props["can_shrink"] = value

    def set_child(self, value):
        self.child = value

    def get_child(self):
        return self.child

    def set_from_icon_name(self, value):
        self.props["icon"] = value

    def add_css_class(self, value):
        self.props.setdefault("css", []).append(value)


class HeaderBar(Widget):
    pass


class Menu(Widget):
    pass


class PopoverMenu(Widget):
    @classmethod
    def new_from_model(cls, model):
        widget = cls()
        widget.model = model
        return widget


class StringObject:
    def __init__(self, text):
        self.text = text

    def get_string(self):
        return self.text


class Factory(Widget):
    pass


GTK = NS(
    Label=Widget, Box=Widget, Button=Widget,
    HeaderBar=HeaderBar, DropDown=Widget, MenuButton=Widget,
    PopoverMenu=PopoverMenu, SignalListItemFactory=Factory,
    StringObject=StringObject,
    Orientation=NS(VERTICAL=0, HORIZONTAL=1),
)
ADW = NS(HeaderBar=HeaderBar)
GIO = NS(Menu=Menu)
PANGO = NS(EllipsizeMode=NS(END="end", MIDDLE="middle"))


def _window(plugin_manager=None):
    events = []
    window = NS(
        plugin_manager=plugin_manager,
        _closed=False,
        diagnostics=NS(add=lambda *args, **kwargs:
                       events.append(("diagnostic", args, kwargs))),
        _UiHost=lambda owner: NS(owner=owner),
        go_back=lambda: events.append(("back",)),
        go_forward=lambda: events.append(("forward",)),
        _show_statistics=lambda: events.append(("stats",)),
        _identity_changed=lambda *args: events.append(("identity",)),
        _account_changed=lambda *args: events.append(("account",)),
        _update_navigation_buttons=lambda: events.append(("nav-update",)),
        _ensure_pages_menu_rightmost=lambda: events.append(("menu-rightmost",)),
    )
    return window, events


def _build(window):
    return create_shell_header(window, Gtk=GTK, Adw=ADW, Gio=GIO, Pango=PANGO)


def test_statistics_counter_can_shrink_without_changing_text_tooltip_or_action():
    window, events = _window()
    _build(window)
    button = window.statistics_button
    assert button.props["can_shrink"] is True
    caption = "9999 livres · 8888 prêts de bibliothèque"
    button.set_label(caption)
    assert button.props["label"] == caption
    assert "Statistiques de l’identité" in button.tooltip
    button.click()
    assert ("stats",) in events


def test_statistics_counter_older_gtk_ellipsizes_existing_label(monkeypatch):
    monkeypatch.setattr(Widget, "set_can_shrink", None)
    def button(**kwargs):
        widget = Widget(**kwargs)
        widget.child = Widget(label=kwargs.get("label", ""))
        return widget
    monkeypatch.setattr(GTK, "Button", button)
    window, events = _window()
    _build(window)
    assert window.statistics_button.child.props["ellipsize"] == "end"
    window.statistics_button.click()
    assert ("stats",) in events


def test_gtk_independent_controller_and_mainwindow_facade_contract():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in s or "main_window" in s for s in imports)
    win_tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    main = next(n for n in win_tree.body
                if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    facade = next(n for n in main.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_create_shell_header")
    assert [arg.arg for arg in facade.args.args] == ["self"]
    value = ast.unparse(facade)
    assert "shell_header_controller.create_shell_header" in value
    for name in ("Gtk", "Adw", "Gio", "Pango"):
        assert f"{name}={name}" in value


def test_no_plugin_still_renders_stable_shell_header_and_empty_menu():
    window, events = _window()
    header = _build(window)
    assert isinstance(header, HeaderBar)
    assert window._titlebar_service is None
    assert header.title.children == [window.header_title, window.header_subtitle]
    assert window.header_title.props["label"] == "cifereca"
    assert events[0] == ("nav-update",)
    assert events[-1] == ("menu-rightmost",)
    assert header.end is window._header_controls
    assert window._header_controls.children == [
        window.identity_dropdown,
        window.account_dropdown,
        window.statistics_button,
        window.pages_menu_button,
    ]
    assert window.pages_menu_button.popover is window.shell_menu
    assert isinstance(window.shell_menu.model, Menu)
    assert not any(event[0] == "diagnostic" for event in events)


@pytest.mark.parametrize("stage", ["select", "resolve", "build", "wrong-type"])
def test_broken_optional_titlebar_falls_back_to_adw_and_records_only_build_errors(stage):
    events = []
    class Plugin:
        def select(self, name):
            events.append(("select", name))
            if stage == "select":
                raise RuntimeError("private selector detail")
            return NS(descriptor=NS(id="plugin.titlebar"))

        def service(self, plugin_id):
            events.append(("service", plugin_id))
            if stage == "resolve":
                raise ValueError("missing UI service")
            return NS(build=lambda host: (
                (_ for _ in ()).throw(RuntimeError("bad header"))
                if stage == "build" else Widget()
            ))
    window, info = _window(Plugin())
    header = _build(window)
    assert isinstance(header, HeaderBar)
    assert window._titlebar_service is None
    assert events[0] == ("select", "ui.titlebar")
    diagnostic = [row for row in info if row[0] == "diagnostic"]
    if stage in {"build", "wrong-type"}:
        assert len(diagnostic) == 1
        assert diagnostic[0][1][:3] == ("PLUGIN", "titlebar build", "error")
        assert diagnostic[0][2]["detail"] == {"code": "titlebar_factory_failed"}
    else:
        assert diagnostic == []
    assert info[-1] == ("menu-rightmost",)


def test_valid_plugin_header_retains_plugin_object_and_no_duplicate_build():
    built = []
    provided = HeaderBar()
    class Plugin:
        def select(self, name):
            return NS(descriptor=NS(id="titlebar"))

        def service(self, plugin_id):
            assert plugin_id == "titlebar"
            return NS(build=lambda host: (built.append(host), provided)[1])
    window, events = _window(Plugin())
    header = _build(window)
    assert header is provided
    assert len(built) == 1
    assert built[0].owner is window
    assert window._titlebar_service is not None
    assert not any(evt[0] == "diagnostic" for evt in events)


def test_back_forward_statistics_callbacks_keep_window_as_single_owner():
    window, events = _window()
    _build(window)
    window.back_button.click()
    window.forward_button.click()
    window.statistics_button.click()
    assert ("back",) in events
    assert ("forward",) in events
    assert ("stats",) in events
    assert window.back_button.tooltip == "Précédent (Alt+Gauche)"
    assert window.forward_button.tooltip == "Suivant (Alt+Droite)"


def test_identity_and_delegation_notifications_keep_distinct_handlers():
    window, events = _window()
    _build(window)
    window.identity_dropdown.handlers["notify::selected"](None, None)
    window.account_dropdown.handlers["notify::selected"](None, None)
    assert events.count(("identity",)) == 1
    assert events.count(("account",)) == 1
    assert window.identity_dropdown.tooltip == "Identité active"
    assert window.account_dropdown.tooltip == "Délégations de l’identité active"


def test_closed_titlebar_setup_does_not_trigger_plugin_activation():
    # Opening a window deliberately builds header before activating startup
    # plugins; titlebar construction must only request an existing UI service.
    manager = NS(
        activate=lambda: (_ for _ in ()).throw(
            AssertionError("header cannot activate plugins")
        ),
        select=lambda category: None,
    )
    window, events = _window(manager)
    assert isinstance(_build(window), HeaderBar)
    assert not any(evt[0] == "diagnostic" for evt in events)


def test_compact_dropdown_factory_renders_full_tooltip_without_truncating_value():
    window, events = _window()
    _build(window)
    factory = window.identity_dropdown.factory
    setup, bind = factory.handlers["setup"], factory.handlers["bind"]
    row = NS(
        set_child=lambda child: setattr(row, "child", child),
        get_child=lambda: row.child,
        get_item=lambda: StringObject("Identité à libellé long — intacte"),
    )
    setup(factory, row)
    bind(factory, row)
    assert row.child.props["ellipsize"] == "end"
    assert row.child.props["single_line"] is True
    assert row.child.props["label"] == "Identité à libellé long — intacte"
    assert row.child.tooltip == "Identité à libellé long — intacte"
    list_row = NS(set_child=lambda child: setattr(list_row, "child", child))
    list_factory = window.identity_dropdown.list_factory
    list_factory.handlers["setup"](list_factory, list_row)
    assert list_row.child.props["ellipsize"] == "middle"
    assert list_row.child.props["max_width_chars"] == 44


def test_compact_dropdown_factory_handles_missing_string_object():
    window, _events = _window()
    _build(window)
    factory = window.account_dropdown.factory
    row = NS(
        set_child=lambda child: setattr(row, "child", child),
        get_child=lambda: row.child,
        get_item=lambda: None,
    )
    factory.handlers["setup"](factory, row)
    factory.handlers["bind"](factory, row)
    assert row.child.props["label"] == ""
    assert row.child.tooltip == ""
