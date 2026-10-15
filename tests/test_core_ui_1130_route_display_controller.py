"""P0 #1130: routes own only their historical lazy UI refreshes."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.route_display_controller import show_route


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
OWNER = MAIN.with_name("route_display_controller.py")


class GtkWindow:
    pass


GTK = NS(Window=GtkWindow, DirectionType=NS(TAB_FORWARD="tab-forward"))


def _context(*, library=None, recent=None, history=None, bookings=None):
    events = []
    library_entries = ["library-book"] if library is None else library
    recent_entries = ["recent-book"] if recent is None else recent
    service = NS(
        last_history=["history"] if history is None else history,
        last_bookings=["booking"] if bookings is None else bookings,
        current_identity=NS(identity_id="alice"),
    )
    pages = {
        route: NS(title="Title " + route)
        for route in (
            "dashboard", "library", "recent", "history", "bookings",
            "sync", "tools", "settings", "plugins", "search", "custom",
        )
    }
    host = NS(
        _closed=False, _pages=pages, _plugin_page_widgets={},
        service=service,
        view_stack=NS(
            set_visible_child_name=lambda route: events.append(("view", route))
        ),
        header_subtitle=NS(
            set_label=lambda title: events.append(("subtitle", title))
        ),
        _refresh_header_actions=lambda route: events.append(("header", route)),
        _library_entries_cache=library_entries,
        _recent_entries_cache=recent_entries,
        _render_dashboard=lambda: events.append(("dashboard",)),
        _render_library=lambda rows: events.append(("library", rows)),
        refresh_library=lambda scan: events.append(("library-refresh", scan)),
        _render_recent=lambda rows: events.append(("recent", rows)),
        refresh_recent=lambda: events.append(("recent-refresh",)),
        refresh_history=lambda scan: events.append(("history-refresh", scan)),
        refresh_bookings=lambda scan: events.append(("bookings-refresh", scan)),
        _refresh_sync_page=lambda: events.append(("sync",)),
        _refresh_tools_page=lambda: events.append(("tools",)),
        refresh_diagnostics_text=lambda: events.append(("diagnostics",)),
        plugins_page=NS(refresh=lambda: events.append(("plugin-refresh",))),
    )
    return host, events


def test_no_gtk_import_and_stable_main_window_route_signature():
    imports = [ast.unparse(n) for n in ast.walk(ast.parse(OWNER.read_text()))
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(x == "import gi" or x.startswith("from gi ")
                   or "main_window" in x for x in imports)
    main = ast.parse(MAIN.read_text())
    cls = next(n for n in main.body if isinstance(n, ast.ClassDef)
               and n.name == "MainWindow")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_show_route")
    assert [arg.arg for arg in method.args.args] == ["self", "route"]
    assert "route_display_controller.show_route(self, route, Gtk=Gtk)" in ast.unparse(method)


def test_missing_route_and_closed_window_do_not_access_any_widgets():
    host, events = _context()
    show_route(host, "not-registered", Gtk=GTK)
    assert events == []
    host._closed = True
    show_route(host, "library", Gtk=GTK)
    assert events == []
    assert host.service.current_identity.identity_id == "alice"


def test_route_shows_page_title_and_updates_action_context_once():
    host, events = _context()
    show_route(host, "custom", Gtk=GTK)
    assert events == [
        ("view", "custom"),
        ("subtitle", "Title custom"),
        ("header", "custom"),
    ]
    host._pages["custom"] = None
    events.clear()
    show_route(host, "custom", Gtk=GTK)
    assert events == [
        ("view", "custom"), ("subtitle", ""), ("header", "custom"),
    ]


def test_search_route_does_not_trigger_network_or_library_refresh():
    host, events = _context()
    show_route(host, "search", Gtk=GTK)
    assert [e[0] for e in events] == ["view", "subtitle", "header"]


def test_dashboard_renders_after_header_actions():
    host, events = _context()
    show_route(host, "dashboard", Gtk=GTK)
    assert events[-2:] == [("header", "dashboard"), ("dashboard",)]


def test_library_uses_cached_snapshot_and_skips_scan_when_available():
    cache = [{"key": "book-1"}]
    host, events = _context(library=cache)
    show_route(host, "library", Gtk=GTK)
    assert events == [
        ("view", "library"), ("subtitle", "Title library"),
        ("header", "library"), ("library", cache),
    ]
    assert host._library_entries_cache is cache


def test_empty_library_first_renders_neutral_state_then_refreshes_without_scan():
    host, events = _context(library=[])
    show_route(host, "library", Gtk=GTK)
    assert [e[0] for e in events] == [
        "view", "subtitle", "header", "library", "library-refresh",
    ]
    assert events[-2] == ("library", [])
    assert events[-1] == ("library-refresh", False)


def test_recent_uses_cache_without_source_call_and_empty_recent_refreshes():
    host, events = _context(recent=["entry"])
    show_route(host, "recent", Gtk=GTK)
    assert events[-1] == ("recent", ["entry"])
    assert not any(e[0] == "recent-refresh" for e in events)
    events.clear()
    host._recent_entries_cache = []
    show_route(host, "recent", Gtk=GTK)
    assert events[-2:] == [("recent", []), ("recent-refresh",)]


def test_successfully_empty_library_is_not_refetched_on_each_navigation():
    host, events = _context(library=[])
    host._identity_context_generation = 4
    show_route(host, "library", Gtk=GTK)
    assert events[-1] == ("library-refresh", False)
    # The real refresh callback sets this only after successful display.
    host._library_loaded_generation = 4
    events.clear()
    for _ in range(3):
        show_route(host, "library", Gtk=GTK)
    assert len([event for event in events if event[0] == "library"]) == 3
    assert not any(event[0] == "library-refresh" for event in events)
    # Even when selecting the original identity again, the new generation
    # forces exactly the normal lazy refresh instead of using stale emptiness.
    host._identity_context_generation = 5
    show_route(host, "library", Gtk=GTK)
    assert events[-1] == ("library-refresh", False)


def test_successfully_empty_recent_is_not_refetched_until_identity_changes():
    host, events = _context(recent=[])
    host._identity_context_generation = 2
    show_route(host, "recent", Gtk=GTK)
    assert events[-1] == ("recent-refresh",)
    host._recent_loaded_generation = 2
    events.clear()
    show_route(host, "recent", Gtk=GTK)
    show_route(host, "recent", Gtk=GTK)
    assert not any(event[0] == "recent-refresh" for event in events)
    host._identity_context_generation = 3
    show_route(host, "recent", Gtk=GTK)
    assert events[-1] == ("recent-refresh",)


def test_history_and_booking_refresh_only_when_cached_service_data_is_empty():
    host, events = _context(history=[], bookings=[])
    show_route(host, "history", Gtk=GTK)
    show_route(host, "bookings", Gtk=GTK)
    assert ("history-refresh", False) in events
    assert ("bookings-refresh", False) in events
    events.clear()
    host.service.last_history = ["old"]
    host.service.last_bookings = ["booking"]
    show_route(host, "history", Gtk=GTK)
    show_route(host, "bookings", Gtk=GTK)
    assert not any(e[0].endswith("-refresh") for e in events)


def test_sync_tools_and_settings_do_not_trigger_unrelated_page_refreshes():
    host, events = _context()
    for route in ("sync", "tools", "settings"):
        show_route(host, route, Gtk=GTK)
    assert [e for e in events if e[0] in {"sync", "tools", "diagnostics"}] == [
        ("sync",), ("tools",), ("diagnostics",),
    ]
    assert not any(e[0].startswith("library") for e in events)


def test_plugin_page_refreshes_only_if_its_widget_offers_the_capability():
    host, events = _context()
    show_route(host, "plugins", Gtk=GTK)
    assert events[-1] == ("plugin-refresh",)
    events.clear()
    host.plugins_page = NS()
    show_route(host, "plugins", Gtk=GTK)
    assert events == [
        ("view", "plugins"), ("subtitle", "Title plugins"),
        ("header", "plugins"),
    ]


def test_focused_plugin_page_uses_existing_gtk_window_root_and_tab_forward():
    focus_events = []
    host, events = _context()
    host._plugin_page_widgets["custom"] = NS(
        get_root=lambda: GtkWindow(),
        child_focus=lambda direction: focus_events.append(direction),
    )
    show_route(host, "custom", Gtk=GTK)
    assert focus_events == ["tab-forward"]
    focus_events.clear()
    host._plugin_page_widgets["custom"] = NS(
        get_root=lambda: object(),
        child_focus=lambda direction: focus_events.append(direction),
    )
    show_route(host, "custom", Gtk=GTK)
    assert focus_events == []


def test_closed_during_maintenance_disposal_skips_detached_route_widgets():
    host, events = _context()
    def dispose():
        events.append(("dispose",))
        host._closed = True
    host._dispose_maintenance_lines = dispose
    show_route(host, "library", Gtk=GTK)
    assert events == [("dispose",)]


def test_closed_by_visible_child_signal_skips_header_and_render():
    host, events = _context()
    def select(route):
        events.append(("view", route))
        host._closed = True
    host.view_stack.set_visible_child_name = select
    show_route(host, "library", Gtk=GTK)
    assert events == [("view", "library")]


def test_closed_by_subtitle_callback_skips_header_and_content():
    host, events = _context()
    def subtitle(value):
        events.append(("subtitle", value))
        host._closed = True
    host.header_subtitle.set_label = subtitle
    show_route(host, "library", Gtk=GTK)
    assert events == [("view", "library"), ("subtitle", "Title library")]


def test_closed_by_header_callback_skips_page_render_and_network_refresh():
    host, events = _context(library=[])
    def header(route):
        events.append(("header", route))
        host._closed = True
    host._refresh_header_actions = header
    show_route(host, "library", Gtk=GTK)
    assert [row[0] for row in events] == ["view", "subtitle", "header"]


def test_closed_during_plugin_widget_root_skips_focus_and_render():
    host, events = _context()
    focus = []
    def root():
        host._closed = True
        return GtkWindow()
    host._plugin_page_widgets["custom"] = NS(
        get_root=root, child_focus=lambda direction: focus.append(direction),
    )
    show_route(host, "custom", Gtk=GTK)
    assert focus == []
    assert [row[0] for row in events] == ["view", "subtitle", "header"]


def test_closed_during_plugin_focus_skips_following_route_refresh():
    host, events = _context(library=[])
    def focus(direction):
        events.append(("focus", direction))
        host._closed = True
    host._plugin_page_widgets["library"] = NS(
        get_root=GtkWindow, child_focus=focus,
    )
    show_route(host, "library", Gtk=GTK)
    assert [row[0] for row in events] == ["view", "subtitle", "header", "focus"]


def test_closed_during_empty_library_render_does_not_schedule_lazy_refresh():
    host, events = _context(library=[])
    def render(rows):
        events.append(("library", rows))
        host._closed = True
    host._render_library = render
    show_route(host, "library", Gtk=GTK)
    assert [row[0] for row in events] == ["view", "subtitle", "header", "library"]


def test_closed_during_empty_recent_render_does_not_schedule_lazy_refresh():
    host, events = _context(recent=[])
    def render(rows):
        events.append(("recent", rows))
        host._closed = True
    host._render_recent = render
    show_route(host, "recent", Gtk=GTK)
    assert [row[0] for row in events] == ["view", "subtitle", "header", "recent"]


def test_nested_route_selection_wins_over_superseded_page_render():
    host, events = _context(library=[])
    def header(route):
        events.append(("header", route))
        if route == "library":
            show_route(host, "search", Gtk=GTK)
    host._refresh_header_actions = header
    show_route(host, "library", Gtk=GTK)
    assert events == [
        ("view", "library"), ("subtitle", "Title library"), ("header", "library"),
        ("view", "search"), ("subtitle", "Title search"), ("header", "search"),
    ]


def test_nested_route_selection_from_render_skips_obsolete_lazy_refresh():
    host, events = _context(library=[])
    def render(rows):
        events.append(("library", rows))
        show_route(host, "search", Gtk=GTK)
    host._render_library = render
    show_route(host, "library", Gtk=GTK)
    assert [row[0] for row in events] == [
        "view", "subtitle", "header", "library", "view", "subtitle", "header",
    ]
    assert not any(row[0] == "library-refresh" for row in events)


def test_route_close_never_reenters_disposed_widget_tree():
    host = NS(_closed=True)
    show_route(host, "library", Gtk=GTK)
