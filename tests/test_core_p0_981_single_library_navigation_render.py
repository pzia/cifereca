"""#981: history return does not rebuild Library after its route already did."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.navigation_controller import restore_navigation_state
from cifereca.gui.navigation_history import NavigationState
from cifereca.gui.route_display_controller import show_route


def _window():
    calls = []
    selected_filter = ["original"]
    selected_identity = NS(identity_id="alice")

    class GtkWindow:
        pass

    gtk = NS(Window=GtkWindow, DirectionType=NS(TAB_FORWARD="forward"))
    host = NS(
        _closed=False,
        _pages={"library": NS(title="Bibliothèque"),
                "search": NS(title="Recherche")},
        _plugin_page_widgets={},
        _restoring_navigation=False,
        service=NS(
            current_identity=selected_identity,
            identity_by_id=lambda identity_id: (
                selected_identity if identity_id == "alice" else None
            ),
        ),
        view_stack=NS(set_visible_child_name=lambda route: calls.append(("route", route))),
        header_subtitle=NS(set_label=lambda title: calls.append(("subtitle", title))),
        _refresh_header_actions=lambda route: calls.append(("header", route)),
        _library_entries_cache=[{"key": "book-1"}],
        library_controls=NS(set_state=lambda value: selected_filter.__setitem__(0, value)),
        nav_rows={"library": object(), "search": object()},
        nav_list=NS(select_row=lambda row: calls.append(("nav", row))),
        _update_navigation_buttons=lambda: calls.append(("buttons",)),
        _render_library=lambda entries: calls.append(
            ("library-render", tuple(entry["key"] for entry in entries), selected_filter[0])
        ),
        _render_search_results=lambda: calls.append(("search-render",)),
        refresh_library=lambda force: calls.append(("unexpected-scan", force)),
    )
    host._show_route = lambda route: show_route(host, route, Gtk=gtk)
    return host, calls, selected_filter


def test_library_history_return_has_one_route_owned_render_and_no_scan():
    host, calls, selected_filter = _window()
    restore_navigation_state(host, NavigationState("library", "alice", "restored"))
    assert selected_filter == ["restored"]
    assert [event for event in calls if event[0] == "library-render"] == [
        ("library-render", ("book-1",), "restored"),
    ]
    assert [event for event in calls if event[0] == "route"] == [
        ("route", "library"),
    ]
    assert not any(event[0] == "unexpected-scan" for event in calls)
    assert host._restoring_navigation is False


def test_repeated_back_forward_to_library_renders_once_per_explicit_restore():
    host, calls, selected_filter = _window()
    for index in range(3):
        restore_navigation_state(
            host, NavigationState("library", "alice", f"filter-{index}"),
        )
    assert selected_filter == ["filter-2"]
    assert [event[2] for event in calls if event[0] == "library-render"] == [
        "filter-0", "filter-1", "filter-2",
    ]
    assert len([event for event in calls if event[0] == "library-render"]) == 3
    assert not any(event[0] == "unexpected-scan" for event in calls)


def test_search_preserves_its_explicit_result_render():
    host, calls, _filter = _window()
    host.search_controls = NS(set_state=lambda value: calls.append(("query", value)))
    restore_navigation_state(host, NavigationState("search", "alice", "forest"))
    assert ("query", "forest") in calls
    assert len([event for event in calls if event[0] == "search-render"]) == 1
    assert not any(event[0] == "library-render" for event in calls)


def test_removed_identity_never_renders_cached_library_for_other_profile():
    host, calls, _filter = _window()
    restore_navigation_state(host, NavigationState("library", "deleted", "PRIVATE"))
    assert not any(event[0] in {"library-render", "route"} for event in calls)
    assert host.service.current_identity.identity_id == "alice"
    assert host._restoring_navigation is False
