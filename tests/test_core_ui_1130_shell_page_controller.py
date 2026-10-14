"""P0 #1130: shell page reconstruction and plugin route isolation."""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import types
from types import SimpleNamespace as NS

from cifereca.gui import shell_page_controller
from cifereca.gui.shell_model import resolve_pages
from cifereca.plugin_api import UiPageContribution


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "shell_page_controller.py"


class Widget:
    def __init__(self, *args, **kwargs):
        self.children = []
        self.visible = True
        self.active = False

    def set_visible(self, state):
        self.visible = state

    def set_margin_top(self, *_): pass
    def set_margin_bottom(self, *_): pass
    def set_margin_start(self, *_): pass
    def set_margin_end(self, *_): pass
    def set_hexpand(self, *_): pass
    def set_ellipsize(self, *_): pass
    def set_child(self, child):
        self.child = child
    def set_active(self, value):
        self.active = value
    def set_tooltip_text(self, *_): pass
    def set_selectable(self, *_): pass
    def set_activatable(self, *_): pass
    def connect(self, *_): pass
    def append(self, child):
        self.children.append(child)


class Label(Widget):
    def __init__(self, *args, **kwargs):
        super().__init__()


GTK = NS(Widget=Widget, Label=Label, ToggleButton=Widget, ListBoxRow=Widget)
PANGO = NS(EllipsizeMode=NS(END=1))


def _page(route, *, group="usage", priority=0, factory=None):
    return UiPageContribution(
        route=route, title=route.title(), group=group, priority=priority,
        order={"dashboard": 0, "search": 20, "library": 30, "settings": 40, "plugins": 50}.get(route, 100),
        icon_name="dialog-information-symbolic",
        factory=factory or (lambda _host: Widget()),
    )


def _host(monkeypatch, *, contributions=(), visible="search", broken=False):
    old_search, old_library = Widget(), Widget()
    removed, names, navigation, events = [], [], [], []
    view = NS(
        get_visible_child_name=lambda: visible,
        remove=lambda widget: removed.append(widget),
        add_named=lambda widget, route: names.append(route),
    )
    search = NS(state=lambda: "search-query", set_state=lambda state: events.append(("search", state)))
    library = NS(state=lambda: "library-query", set_state=lambda state: events.append(("library", state)))
    fallback = [
        _page("dashboard"),
        _page("search"),
        _page("library"),
        _page("settings", group="configuration"),
        _page("plugins", group="configuration"),
    ]
    stub = types.ModuleType("cifereca.builtin_ui.cifereca_builtin_ui")
    stub.Pages = lambda: NS(pages=lambda: fallback)
    monkeypatch.setitem(sys.modules, stub.__name__, stub)

    def page_contributions():
        if broken:
            raise RuntimeError("plugin unavailable")
        return list(contributions)

    host = NS(
        _overview_listeners=["stale"], view_stack=view,
        _plugin_page_widgets={"search": old_search, "library": old_library},
        search_controls=search, library_controls=library,
        nav_list=Widget(), nav_rows={"stale": Widget()},
        _nav_groups={"stale": "usage"}, _nav_group_expanded={},
        plugin_manager=NS(page_contributions=page_contributions),
        diagnostics=NS(add=lambda *args, **kw: events.append(("diagnostic", args))),
        _UiHost=lambda window: NS(window=window),
        _add_nav_row=lambda route, title, icon: host.nav_rows.__setitem__(route, Widget()),
        navigate=navigation.append,
    )
    return host, removed, names, navigation, events


def _rebuild(host):
    return shell_page_controller.rebuild_plugin_pages(
        host, Gtk=GTK, Pango=PANGO,
        clear_box=lambda container: container.children.clear(),
        resolve_pages=resolve_pages,
    )


def test_shell_coordinator_is_headless_and_facade_stable():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(x == "import gi" or x.startswith("from gi ")
                   or "main_window" in x for x in imports)
    window = next(node for node in ast.parse(MAIN.read_text(encoding="utf-8")).body
                  if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in window.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_rebuild_plugin_pages")
    assert [a.arg for a in method.args.args] == ["self"]
    assert "shell_page_controller.rebuild_plugin_pages" in ast.unparse(method)
    assert "Gtk=Gtk" in ast.unparse(method)
    assert "Pango=Pango" in ast.unparse(method)


def test_rebuild_preserves_filters_and_active_route_and_clears_old_widgets(monkeypatch):
    host, removed, names, navigation, events = _host(
        monkeypatch, contributions=[_page("search"), _page("library")],
    )
    assert _rebuild(host) is None
    assert len(removed) == 2
    assert set(host._pages) == {"search", "library", "settings", "plugins"}
    assert set(host.nav_rows) == set(host._pages)
    assert names == ["search", "library", "settings", "plugins"]
    assert events[:2] == [("search", "search-query"), ("library", "library-query")]
    assert navigation == ["search"]
    assert host._overview_listeners == []
    assert host._nav_group_expanded["configuration"] is False
    assert host.nav_rows["settings"].visible is False


def test_broken_or_absent_plugin_pages_use_fallback_without_losing_settings(monkeypatch):
    host, removed, names, navigation, events = _host(
        monkeypatch, broken=True, visible="removed-page",
    )
    _rebuild(host)
    assert set(host._pages) == {"dashboard", "search", "library", "settings", "plugins"}
    assert navigation == ["dashboard"]
    assert not any(name == "diagnostic" for name, _ in events)
    assert "settings" in names and "plugins" in names


def test_failed_settings_factory_recovers_using_builtin_management_page(monkeypatch):
    def fail(_host):
        raise RuntimeError("broken settings")
    host, removed, names, navigation, events = _host(
        monkeypatch, contributions=[_page("settings", priority=50, factory=fail)],
        visible="settings",
    )
    _rebuild(host)
    assert set(host._pages) == {"settings", "plugins"}
    assert host._pages["settings"].priority == -10000
    assert navigation == ["settings"]
    assert [name for name, _ in events if name == "diagnostic"] == ["diagnostic"]


def test_without_view_stack_does_not_touch_plugin_manager(monkeypatch):
    host, _, _, _, _ = _host(monkeypatch)
    del host.view_stack
    host.plugin_manager = NS(page_contributions=lambda: (_ for _ in ()).throw(
        AssertionError("must not load plugins when shell is missing")
    ))
    assert _rebuild(host) is None
    assert host._overview_listeners == []


def test_closed_shell_never_clears_listeners_or_consults_pages(monkeypatch):
    host, removed, names, navigation, events = _host(monkeypatch)
    host._closed = True
    host.plugin_manager.page_contributions = lambda: (_ for _ in ()).throw(
        AssertionError("do not run plugins after teardown")
    )
    _rebuild(host)
    assert host._overview_listeners == ["stale"]
    assert removed == names == navigation == events == []


def test_closure_during_filter_snapshot_stops_before_widget_removal(monkeypatch):
    host, removed, names, navigation, events = _host(monkeypatch)
    def snapshot():
        host._closed = True
        return "private"
    host.search_controls.state = snapshot
    _rebuild(host)
    assert removed == names == navigation == events == []
    assert host._closed is True


def test_closure_during_widget_removal_stops_other_mutations(monkeypatch):
    host, removed, names, navigation, events = _host(monkeypatch)
    def remove(widget):
        removed.append(widget)
        host._closed = True
    host.view_stack.remove = remove
    _rebuild(host)
    assert len(removed) == 1
    assert host.nav_rows == {"stale": host.nav_rows["stale"]}
    assert names == navigation == events == []


def test_closure_during_plugin_discovery_never_constructs_pages(monkeypatch):
    page_calls = []
    page = _page("search", factory=lambda _: (page_calls.append("factory") or Widget()))
    host, removed, names, navigation, events = _host(monkeypatch, contributions=[page])
    def discovered():
        host._closed = True
        return [page]
    host.plugin_manager.page_contributions = discovered
    _rebuild(host)
    assert page_calls == []
    assert names == navigation == events == []


def test_factory_that_closes_window_does_not_attach_or_render_its_page(monkeypatch):
    def factory(_host):
        host._closed = True
        return Widget()
    host, removed, names, navigation, events = _host(
        monkeypatch, contributions=[_page("search", factory=factory)]
    )
    _rebuild(host)
    assert names == navigation == []
    assert host._plugin_page_widgets == {}
    assert host._pages == {}
    assert events == []


def test_diagnostic_callback_that_disposes_shell_skips_recovery(monkeypatch):
    def factory(_host):
        raise RuntimeError("invalid")
    host, removed, names, navigation, events = _host(
        monkeypatch, contributions=[_page("settings", factory=factory)]
    )
    def diagnose(*args, **kwargs):
        events.append(("diagnostic", args))
        host._closed = True
    host.diagnostics.add = diagnose
    _rebuild(host)
    assert [item[0] for item in events] == ["diagnostic"]
    assert names == navigation == []
    assert host._pages == {}


def test_close_during_named_page_attachment_aborts_nav_rebuild(monkeypatch):
    host, removed, names, navigation, events = _host(
        monkeypatch, contributions=[_page("search")]
    )
    def add(widget, route):
        names.append(route)
        host._closed = True
    host.view_stack.add_named = add
    _rebuild(host)
    assert names == ["search"]
    assert navigation == []
    assert host._plugin_page_widgets == {}
    assert host._pages == {}


def test_close_during_nav_row_addition_skips_group_visibility_and_navigation(monkeypatch):
    host, removed, names, navigation, events = _host(
        monkeypatch, contributions=[_page("search")]
    )
    def add_row(route, title, icon):
        host.nav_rows[route] = Widget()
        host._closed = True
    host._add_nav_row = add_row
    _rebuild(host)
    assert names == ["search"]
    assert navigation == []
    assert host._nav_groups == {}


def test_close_during_filter_restore_does_not_restore_other_route_or_navigate(monkeypatch):
    host, removed, names, navigation, events = _host(
        monkeypatch, contributions=[_page("search"), _page("library")]
    )
    def set_state(value):
        events.append(("search", value))
        host._closed = True
    host.search_controls.set_state = set_state
    _rebuild(host)
    assert names == ["search", "library", "settings", "plugins"]
    assert events == [("search", "search-query")]
    assert navigation == []


def test_recursive_plugin_rebuild_does_not_resume_stale_outer_rebuild(monkeypatch):
    invoked = [False]
    def factory(_host):
        if not invoked[0]:
            invoked[0] = True
            _rebuild(host)
        return Widget()
    host, removed, names, navigation, events = _host(
        monkeypatch, contributions=[_page("search", factory=factory)]
    )
    _rebuild(host)
    # The outer builder cannot duplicate widgets/groups/route navigation
    # after its nested successor has installed the new generation.
    assert names == ["search", "settings", "plugins"]
    assert navigation == ["search"]
    assert set(host._pages) == {"search", "settings", "plugins"}


def test_old_group_toggle_is_inert_after_rebuild_or_close(monkeypatch):
    callbacks = []
    class Toggle(Widget):
        def connect(self, signal, callback):
            assert signal == "toggled"
            callbacks.append((self, callback))
        def get_active(self):
            return self.active
    monkeypatch.setattr(GTK, "ToggleButton", Toggle)
    host, removed, names, navigation, events = _host(
        monkeypatch, contributions=[_page("search")]
    )
    toggled = []
    host._toggle_nav_group = lambda group, value: toggled.append((group, value))
    _rebuild(host)
    old_toggle, old_callback = callbacks[0]
    old_callback(old_toggle)
    assert toggled == [("usage", True)]
    _rebuild(host)
    toggled.clear()
    old_callback(old_toggle)
    assert toggled == []
    current_toggle, current_callback = callbacks[-2]  # usage on second rebuild
    current_callback(current_toggle)
    assert toggled == [("usage", True)]
    host._closed = True
    current_callback(current_toggle)
    assert toggled == [("usage", True)]
