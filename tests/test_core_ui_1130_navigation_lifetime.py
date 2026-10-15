"""#1130: no navigation or GTK mutation after shell teardown (headless)."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui import navigation_controller as nav
from cifereca.gui.navigation_history import NavigationHistory, NavigationState


OWNER = Path(__file__).resolve().parents[1] / "src/cifereca/gui/navigation_controller.py"


def _host():
    events = []
    history = NavigationHistory()
    row = NS(get_name=lambda: "library")
    host = NS(
        _closed=False, _pages={"library": object(), "search": object()},
        service=NS(
            current_identity=NS(identity_id="alice"),
            identity_by_id=lambda ident: NS(identity_id=ident),
        ),
        _restoring_navigation=False, _navigation_history=history,
        nav_rows={"library": row},
        nav_list=NS(
            get_selected_row=lambda: row,
            select_row=lambda selected: events.append(("select", selected)),
        ),
        library_controls=NS(
            state=lambda: "local query",
            set_state=lambda state: events.append(("filters", state)),
        ),
        _show_route=lambda route: events.append(("show", route)),
        _update_navigation_buttons=lambda: events.append(("buttons",)),
        _render_library=lambda: events.append(("library",)),
        _render_search_results=lambda: events.append(("search",)),
        _activate_identity=lambda ident: events.append(("activate", ident)),
    )
    host._navigation_state = lambda route: nav.navigation_state(host, route)
    host._visit_route = lambda route: nav.visit_route(host, route)
    host._restore_navigation_state = lambda state: nav.restore_navigation_state(host, state)
    return host, events, history


def test_import_boundary_does_not_acquire_gtk_or_main_window():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("main_window" in name or name == "import gi"
                   or name.startswith("from gi ") for name in imports)


def test_all_navigation_entrypoints_are_inert_after_close():
    host, events, history = _host()
    history.visit(NavigationState("library", "alice", "one"))
    history.visit(NavigationState("search", "alice", "two"))
    before = (history.current, tuple(history._back), tuple(history._forward))
    host._closed = True
    nav.visit_route(host, "library")
    nav.navigate(host, "library")
    nav.nav_selected(host, None, host.nav_rows["library"])
    nav.restore_navigation_state(host, NavigationState("library", "alice", "secret"))
    nav.update_navigation_buttons(host)
    nav.go_back(host)
    nav.go_forward(host)
    assert nav.can_go_back(host) is False
    assert nav.can_go_forward(host) is False
    assert (history.current, tuple(history._back), tuple(history._forward)) == before
    assert events == []


def test_closed_host_never_reads_disposed_navigation_widgets():
    host = NS(_closed=True)
    nav.navigate(host, "library")
    nav.nav_selected(host, None, object())
    nav.visit_route(host, "library")
    nav.restore_navigation_state(host, NavigationState("library"))
    nav.go_back(host)
    nav.go_forward(host)
    nav.update_navigation_buttons(host)


def test_visit_aborts_if_its_state_callback_closes_the_window():
    host, events, history = _host()
    def state(route):
        host._closed = True
        return NavigationState(route, "alice", "query")
    host._navigation_state = state
    nav.visit_route(host, "library")
    assert history.current is None
    assert events == []


def test_visit_does_not_update_buttons_after_route_disposes_window():
    host, events, history = _host()
    def show(route):
        events.append(("show", route))
        host._closed = True
    host._show_route = show
    nav.visit_route(host, "library")
    assert history.current == NavigationState("library", "alice", "local query")
    assert events == [("show", "library")]


def test_restore_aborts_after_identity_activation_closes_window():
    host, events, _ = _host()
    def activate(ident):
        events.append(("activate", ident))
        host._closed = True
    host._activate_identity = activate
    nav.restore_navigation_state(host, NavigationState("library", "bob", "secret"))
    assert events == [("activate", "bob")]
    assert host._restoring_navigation is False


def test_restore_aborts_after_filter_callback_closes_window():
    host, events, _ = _host()
    def change(state):
        events.append(("filters", state))
        host._closed = True
    host.library_controls.set_state = change
    nav.restore_navigation_state(host, NavigationState("library", "alice", "private"))
    assert events == [("filters", "private")]
    assert host._restoring_navigation is False


def test_restore_aborts_after_nav_selection_closes_window():
    host, events, _ = _host()
    def select(row):
        events.append(("select", row))
        host._closed = True
    host.nav_list.select_row = select
    nav.restore_navigation_state(host, NavigationState("library", "alice", None))
    assert [item[0] for item in events] == ["select"]
    assert host._restoring_navigation is False


def test_restore_does_not_render_if_show_route_closes_window():
    host, events, _ = _host()
    def show(route):
        events.append(("show", route))
        host._closed = True
    host._show_route = show
    nav.restore_navigation_state(host, NavigationState("library", "alice", None))
    assert [item[0] for item in events] == ["select", "show"]
    assert host._restoring_navigation is False


def test_live_back_and_forward_still_restore_original_routes_and_filters():
    host, events, history = _host()
    history.visit(NavigationState("library", "alice", "first"))
    history.visit(NavigationState("search", "alice", "second"))
    nav.go_back(host)
    assert history.current.route == "library"
    assert ("filters", "first") in events
    assert ("library",) in events
    events.clear()
    nav.go_forward(host)
    assert history.current.route == "search"
    assert ("show", "search") in events
    assert ("search",) in events
    assert host._restoring_navigation is False
