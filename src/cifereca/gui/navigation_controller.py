"""GTK-free navigation coordinator; MainWindow retains widget/identity ownership (#1130)."""
from __future__ import annotations

from typing import Any, Protocol
from .navigation_history import NavigationState


class NavigationHost(Protocol):
    service: Any
    _pages: Any
    _navigation_history: Any
    _restoring_navigation: bool
    nav_rows: Any
    nav_list: Any

    def _navigation_state(self, route: str) -> NavigationState: ...
    def _update_navigation_buttons(self) -> None: ...
    def _show_route(self, route: str) -> None: ...
    def _activate_identity(self, identity_id: str) -> None: ...
    def _render_library(self) -> None: ...
    def _render_search_results(self) -> None: ...
    def _visit_route(self, route: str) -> None: ...
    def _restore_navigation_state(self, state: NavigationState | None) -> None: ...


def navigation_state(host: NavigationHost, route: str) -> NavigationState:
    identity = host.service.current_identity
    controls = getattr(host, route + "_controls", None)
    query_state = controls.state() if controls is not None else None
    return NavigationState(route, identity.identity_id if identity else None, query_state)


def update_navigation_buttons(host: NavigationHost) -> None:
    if getattr(host, "_closed", False):
        return
    history = getattr(host, "_navigation_history", None)
    if hasattr(host, "back_button"):
        host.back_button.set_sensitive(bool(history and history.can_back()))
    if hasattr(host, "forward_button"):
        host.forward_button.set_sensitive(bool(history and history.can_forward()))


def visit_route(host: NavigationHost, route: str) -> None:
    if getattr(host, "_closed", False) or route not in host._pages:
        return
    if not getattr(host, "_restoring_navigation", False):
        state = host._navigation_state(route)
        if getattr(host, "_closed", False):
            return
        host._navigation_history.visit(state)
    if getattr(host, "_closed", False):
        return
    host._show_route(route)
    if not getattr(host, "_closed", False):
        host._update_navigation_buttons()


def restore_navigation_state(host: NavigationHost, state: NavigationState | None) -> None:
    if getattr(host, "_closed", False):
        return
    if state is None or state.route not in host._pages:
        host._update_navigation_buttons()
        return
    host._restoring_navigation = True
    try:
        current = host.service.current_identity
        if state.identity_id:
            # Stale current_identity is not proof an identity still exists.
            if host.service.identity_by_id(state.identity_id) is None:
                return
            if current is None or current.identity_id != state.identity_id:
                host._activate_identity(state.identity_id)
                if getattr(host, "_closed", False):
                    return
        controls = getattr(host, state.route + "_controls", None)
        if controls is not None and state.query_state is not None:
            controls.set_state(state.query_state)
            if getattr(host, "_closed", False):
                return
        row = host.nav_rows.get(state.route)
        if row is not None:
            host.nav_list.select_row(row)
            if getattr(host, "_closed", False):
                return
        host._show_route(state.route)
        if getattr(host, "_closed", False):
            return
        if state.route == "library":
            host._render_library()
        elif state.route == "search":
            host._render_search_results()
    finally:
        host._restoring_navigation = False
        if not getattr(host, "_closed", False):
            host._update_navigation_buttons()


def can_go_back(host: NavigationHost) -> bool:
    return bool(not getattr(host, "_closed", False) and getattr(host, "_navigation_history", None) and host._navigation_history.can_back())


def can_go_forward(host: NavigationHost) -> bool:
    return bool(not getattr(host, "_closed", False) and getattr(host, "_navigation_history", None) and host._navigation_history.can_forward())


def go_back(host: NavigationHost) -> None:
    if not getattr(host, "_closed", False) and getattr(host, "_navigation_history", None):
        host._restore_navigation_state(host._navigation_history.back())


def go_forward(host: NavigationHost) -> None:
    if not getattr(host, "_closed", False) and getattr(host, "_navigation_history", None):
        host._restore_navigation_state(host._navigation_history.forward())


def nav_selected(host: NavigationHost, _listbox: Any, row: Any) -> None:
    if getattr(host, "_closed", False) or row is None:
        return
    host._visit_route(row.get_name() or "library")


def navigate(host: NavigationHost, route: str) -> None:
    if getattr(host, "_closed", False):
        return
    row = host.nav_rows.get(route)
    if row:
        if host.nav_list.get_selected_row() is row:
            host._visit_route(route)
        else:
            host.nav_list.select_row(row)
        return
    host._visit_route(route)
