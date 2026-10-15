"""P0 #1130: GTK-neutral route presentation for MainWindow.

The host owns widgets, identity, cached entries and refresh services. Route
presentation never creates pages or triggers a source scan unless the original
MainWindow route was already responsible for a lazy refresh.
"""
from __future__ import annotations


def show_route(window, route: str, *, Gtk) -> None:
    if getattr(window, "_closed", False) or route not in window._pages:
        return
    # GTK signals can synchronously redirect to another route or close the
    # window. Only the latest invocation may continue rendering its page.
    token = object()
    window._route_display_token = token

    def is_current():
        return (
            not getattr(window, "_closed", False)
            and getattr(window, "_route_display_token", None) is token
        )

    # Preserve the #1492 per-row callback invalidation when leaving Outils.
    if route != "tools":
        dispose_lines = getattr(window, "_dispose_maintenance_lines", None)
        if callable(dispose_lines):
            dispose_lines()
    if not is_current():
        return
    # UX #962: retain the *actual focused action*, not an arbitrary first
    # tab stop, only for a deliberate Jeunesse -> Recherche navigation.
    # This is scoped to the currently mounted plugin widget and is never a
    # request to refetch provider data or change the selected book.
    outgoing = window.view_stack.get_visible_child_name()
    if outgoing == "youth" and route == "search":
        current_focus = getattr(window, "get_focus", lambda: None)()
        old_widget = window._plugin_page_widgets.get("youth")
        button_type = getattr(Gtk, "Button", None)
        if (
            button_type is not None and isinstance(current_focus, button_type)
            and old_widget is not None and old_widget.is_ancestor(current_focus)
            and current_focus.get_mapped() and current_focus.get_sensitive()
        ):
            identity = getattr(getattr(window.service, "current_identity", None), "identity_id", None)
            window._youth_return_focus = (current_focus, identity)
        else:
            window._youth_return_focus = None
    elif route != "youth" and not getattr(window, "_restoring_navigation", False):
        window._youth_return_focus = None
    window.view_stack.set_visible_child_name(route)
    if not is_current():
        return
    page = window._pages.get(route)
    window.header_subtitle.set_label(page.title if page else "")
    if not is_current():
        return
    window._refresh_header_actions(route)
    if not is_current():
        return
    widget = window._plugin_page_widgets.get(route)
    if widget:
        root = widget.get_root()
        if not is_current():
            return
        if isinstance(root, Gtk.Window):
            widget.child_focus(Gtk.DirectionType.TAB_FORWARD)
            if not is_current():
                return

    if route == "dashboard":
        window._render_dashboard()
    elif route == "library":
        window._render_library(window._library_entries_cache)
        if is_current() and not window._library_entries_cache:
            window.refresh_library(False)
    elif route == "recent":
        window._render_recent(window._recent_entries_cache)
        if is_current() and not window._recent_entries_cache:
            window.refresh_recent()
    elif route == "history" and not window.service.last_history:
        if is_current():
            window.refresh_history(False)
    elif route == "bookings" and not window.service.last_bookings:
        if is_current():
            window.refresh_bookings(False)
    elif route == "sync":
        window._refresh_sync_page()
    elif route == "tools":
        window._refresh_tools_page()
    elif route == "settings":
        window.refresh_diagnostics_text()
    elif route == "plugins" and hasattr(window.plugins_page, "refresh"):
        if is_current():
            window.plugins_page.refresh()

    # Returning through history (not a normal tab switch) restores the exact
    # action that launched the search, iff it is still the same mapped button
    # under the same identity. Invalidated/disposed actions are never revived.
    if route == "youth" and is_current() and getattr(window, "_restoring_navigation", False):
        saved = getattr(window, "_youth_return_focus", None)
        window._youth_return_focus = None
        if saved is not None:
            focus_widget, identity_id = saved
            current_identity = getattr(getattr(window.service, "current_identity", None), "identity_id", None)
            youth_widget = window._plugin_page_widgets.get("youth")
            if (
                identity_id == current_identity and youth_widget is not None
                and youth_widget.is_ancestor(focus_widget)
                and focus_widget.get_mapped() and focus_widget.get_sensitive()
            ):
                focus_widget.grab_focus()
