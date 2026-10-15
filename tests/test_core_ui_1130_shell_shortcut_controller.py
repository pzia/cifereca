"""P0 #1130: shell keyboard routing and queued callbacks without GTK imports."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.shell_shortcut_controller import (
    cycle_page,
    focus_search,
    install_shell_shortcuts,
    open_pages_menu,
    quit_application,
    show_settings,
    show_shortcuts,
)
from cifereca.gui.shortcuts import SHORTCUTS


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
OWNER = MAIN.with_name("shell_shortcut_controller.py")


class Action:
    def __init__(self, name):
        self.name = name
        self.callback = None

    @classmethod
    def new(cls, name, _parameter):
        return cls(name)

    def connect(self, signal, callback):
        assert signal == "activate"
        self.callback = callback

    def activate(self):
        return self.callback(self, None)


def _application_host():
    events, actions, accelerators = [], {}, {}
    app = NS(
        set_accels_for_action=lambda name, keys: accelerators.__setitem__(name, keys),
    )
    host = NS(
        _closed=False,
        get_application=lambda: app,
        add_action=lambda action: actions.__setitem__(action.name, action),
        _quit_application=lambda: events.append(("quit",)),
        close=lambda: events.append(("close",)),
        _open_pages_menu=lambda: events.append(("pages-menu",)),
        _focus_search=lambda: events.append(("focus-search",)),
        _show_settings=lambda: events.append(("settings",)),
        _cycle_page=lambda direction: events.append(("cycle", direction)),
        _show_shortcuts=lambda: events.append(("help",)),
        reconnect_sources=lambda: events.append(("reconnect",)),
        go_back=lambda: events.append(("back",)),
        go_forward=lambda: events.append(("forward",)),
    )
    return host, events, actions, accelerators


def test_controller_is_headless_and_exact_facade_signatures_remain_stable():
    owner = ast.parse(OWNER.read_text(encoding="utf-8"))
    imported = [ast.unparse(n) for n in ast.walk(owner)
                if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(t == "import gi" or t.startswith("from gi ")
                   or "main_window" in t for t in imported)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body if isinstance(n, ast.ClassDef)
               and n.name == "MainWindow")
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    expected = {
        "_install_shell_shortcuts": ("install_shell_shortcuts", ["self"]),
        "_quit_application": ("quit_application", ["self"]),
        "_open_pages_menu": ("open_pages_menu", ["self"]),
        "_focus_search": ("focus_search", ["self"]),
        "_show_settings": ("show_settings", ["self"]),
        "_cycle_page": ("cycle_page", ["self", "direction"]),
        "_show_shortcuts": ("show_shortcuts", ["self"]),
    }
    for name, (target, args) in expected.items():
        method = methods[name]
        assert [a.arg for a in method.args.args] == args
        assert "shell_shortcut_controller." + target + "(" in ast.unparse(method)


def test_registration_preserves_accelerators_and_unique_commands():
    host, events, actions, accelerators = _application_host()
    install_shell_shortcuts(host, Gio=NS(SimpleAction=Action))
    # Reconnect has a menu action but deliberately no dedicated accelerator.
    assert set(actions) == {name for name, _keys, _description in SHORTCUTS} | {"reconnect-sources"}
    assert accelerators == {
        "win." + name: list(keys) for name, keys, _description in SHORTCUTS
    }
    for name in (
        "pages-menu", "focus-search", "settings", "go-back", "go-forward",
        "shortcuts", "reconnect-sources", "close", "quit",
    ):
        actions[name].activate()
    actions["next-page"].activate()
    actions["previous-page"].activate()
    assert events == [
        ("pages-menu",), ("focus-search",), ("settings",),
        ("back",), ("forward",), ("help",), ("reconnect",),
        ("close",), ("quit",), ("cycle", 1), ("cycle", -1),
    ]


def test_queued_shortcut_after_close_cannot_touch_window_or_reconnect():
    host, events, actions, accelerators = _application_host()
    install_shell_shortcuts(host, Gio=NS(SimpleAction=Action))
    host._closed = True
    for action in actions.values():
        action.activate()
    assert events == []


def test_cannot_register_commands_or_accelerators_after_window_close():
    host, events, actions, accelerators = _application_host()
    host._closed = True
    install_shell_shortcuts(host, Gio=NS(SimpleAction=Action))
    assert actions == {}
    assert accelerators == {}
    assert events == []


def test_pages_menu_missing_or_closed_remains_inert():
    open_pages_menu(NS())
    calls = []
    host = NS(pages_menu_button=NS(popup=lambda: calls.append("open")))
    open_pages_menu(host)
    host._closed = True
    open_pages_menu(host)
    assert calls == ["open"]


def test_focus_search_reuses_current_library_filter_without_navigating():
    events = []
    host = NS(
        _pages={"search": object(), "library": object()},
        view_stack=NS(get_visible_child_name=lambda: "library"),
        library_controls=NS(entry=NS(grab_focus=lambda: events.append("library-focus"))),
        search_controls=NS(entry=NS(grab_focus=lambda: events.append("search-focus"))),
        navigate=lambda route: events.append(("navigate", route)),
    )
    focus_search(host)
    assert events == ["library-focus"]


def test_focus_search_from_another_page_navigates_once_then_focuses():
    events = []
    host = NS(
        _pages={"search": object(), "library": object(), "tools": object()},
        view_stack=NS(get_visible_child_name=lambda: "tools"),
        search_controls=NS(entry=NS(grab_focus=lambda: events.append("search-focus"))),
        navigate=lambda route: events.append(("navigate", route)),
    )
    focus_search(host)
    assert events == [("navigate", "search"), "search-focus"]


def test_focus_search_never_invents_a_missing_search_page():
    events = []
    host = NS(
        _pages={"tools": object()},
        view_stack=NS(get_visible_child_name=lambda: "tools"),
        navigate=lambda route: events.append(route),
    )
    focus_search(host)
    assert events == []
    focus_search(NS())
    host._closed = True
    focus_search(host)
    assert events == []


def test_settings_uses_real_route_or_prelogin_diagnostics_as_before():
    events = []
    host = NS(
        _pages={"settings": object()},
        navigate=lambda route: events.append(("navigate", route)),
        _open_prelogin_diagnostics=lambda: events.append(("diagnostics",)),
    )
    show_settings(host)
    assert events == [("navigate", "settings")]
    host._pages = {}
    show_settings(host)
    assert events[-1] == ("diagnostics",)
    host._closed = True
    show_settings(host)
    assert len(events) == 2


def test_cycling_pages_respects_order_direction_and_no_pages_case():
    events = []
    host = NS(
        _pages={"library": object(), "recent": object(), "settings": object()},
        view_stack=NS(get_visible_child_name=lambda: "recent"),
        navigate=lambda route: events.append(route),
    )
    cycle_page(host, 1)
    cycle_page(host, -1)
    assert events == ["settings", "library"]
    host._pages = {}
    cycle_page(host, 1)
    host._closed = True
    cycle_page(host, 1)
    assert events == ["settings", "library"]


def test_shortcut_help_uses_injected_dialog_and_stays_inert_after_close():
    events = []
    class Dialog:
        @classmethod
        def new(cls, window, title, body):
            assert window is host
            assert title == "Raccourcis clavier"
            assert "Ctrl+Q" in body
            return NS(
                add_response=lambda key, text: events.append(("response", key, text)),
                present=lambda: events.append(("present",)),
            )
    host = NS()
    show_shortcuts(host, Adw=NS(MessageDialog=Dialog))
    host._closed = True
    show_shortcuts(host, Adw=NS(MessageDialog=Dialog))
    assert events == [("response", "close", "Fermer"), ("present",)]


def test_quit_closes_before_application_shutdown_and_handles_missing_app():
    events = []
    application = NS(quit=lambda: events.append("quit"))
    window = NS(close=lambda: events.append("close"), get_application=lambda: application)
    quit_application(window)
    assert events == ["close", "quit"]
    events.clear()
    window.get_application = lambda: None
    quit_application(window)
    assert events == ["close"]

def test_close_during_action_creation_stops_registration():
    host, events, actions, accelerators = _application_host()
    class ClosingAction(Action):
        @classmethod
        def new(cls, name, _parameter):
            host._closed = True
            return cls(name)
    install_shell_shortcuts(host, Gio=NS(SimpleAction=ClosingAction))
    assert actions == {} and accelerators == {} and events == []


def test_close_during_first_action_registration_skips_remaining_and_accelerators():
    host, events, actions, accelerators = _application_host()
    def register(action):
        actions[action.name] = action
        host._closed = True
    host.add_action = register
    install_shell_shortcuts(host, Gio=NS(SimpleAction=Action))
    assert len(actions) == 1
    assert accelerators == {}
    for action in actions.values():
        action.activate()
    assert events == []


def test_close_during_application_lookup_skips_accelerators():
    host, events, actions, accelerators = _application_host()
    def application():
        host._closed = True
        return NS(set_accels_for_action=lambda *a: events.append("binding"))
    host.get_application = application
    install_shell_shortcuts(host, Gio=NS(SimpleAction=Action))
    assert len(actions) == 11
    assert accelerators == {} and events == []


def test_close_during_first_accelerator_binding_skips_remaining_bindings():
    host, events, actions, accelerators = _application_host()
    def bind(name, keys):
        accelerators[name] = keys
        host._closed = True
    host.get_application = lambda: NS(set_accels_for_action=bind)
    install_shell_shortcuts(host, Gio=NS(SimpleAction=Action))
    assert len(actions) == 11
    assert len(accelerators) == 1
    assert set(accelerators) == {"win.quit"}


def test_focus_shortcut_aborts_when_visible_route_lookup_closes_window():
    events = []
    host = NS(_closed=False, _pages={"search": object()})
    def selected():
        host._closed = True
        return "search"
    host.view_stack = NS(get_visible_child_name=selected)
    host.search_controls = NS(entry=NS(grab_focus=lambda: events.append("focus")))
    focus_search(host)
    assert events == []


def test_focus_shortcut_aborts_when_navigation_closes_window():
    events = []
    host = NS(
        _closed=False, _pages={"search": object(), "settings": object()},
        view_stack=NS(get_visible_child_name=lambda: "settings"),
        search_controls=NS(entry=NS(grab_focus=lambda: events.append("focus"))),
    )
    def navigate(route):
        events.append(("navigate", route))
        host._closed = True
    host.navigate = navigate
    focus_search(host)
    assert events == [("navigate", "search")]


def test_cycle_shortcut_aborts_when_visible_route_lookup_closes_window():
    events = []
    host = NS(_closed=False, _pages={"search": object(), "library": object()})
    def selected():
        host._closed = True
        return "search"
    host.view_stack = NS(get_visible_child_name=selected)
    host.navigate = events.append
    cycle_page(host, 1)
    assert events == []


def test_shortcut_help_aborts_when_dialog_factory_closes_owner():
    events = []
    host = NS(_closed=False)
    class Dialog:
        @staticmethod
        def new(window, title, body):
            host._closed = True
            return NS(
                add_response=lambda *a: events.append("response"),
                present=lambda: events.append("present"),
            )
    show_shortcuts(host, Adw=NS(MessageDialog=Dialog))
    assert events == []


def test_shortcut_help_aborts_when_response_callback_closes_owner():
    events = []
    host = NS(_closed=False)
    class Dialog:
        @staticmethod
        def new(window, title, body):
            def response(*args):
                events.append("response")
                host._closed = True
            return NS(
                add_response=response,
                present=lambda: events.append("present"),
            )
    show_shortcuts(host, Adw=NS(MessageDialog=Dialog))
    assert events == ["response"]


def test_pages_menu_button_lookup_that_closes_owner_skips_popup():
    events = []
    class Host:
        _closed = False
        @property
        def pages_menu_button(self):
            self._closed = True
            return NS(popup=lambda: events.append("popup"))
    open_pages_menu(Host())
    assert events == []
