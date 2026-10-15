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
        if (
            is_current() and not window._library_entries_cache
            and getattr(window, "_library_loaded_generation", -1)
                != getattr(window, "_identity_context_generation", 0)
        ):
            # A successful empty snapshot is not the same as an uninitialized
            # cache. Reopening Library must not repeatedly query the provider.
            window.refresh_library(False)
    elif route == "recent":
        window._render_recent(window._recent_entries_cache)
        if (
            is_current() and not window._recent_entries_cache
            and getattr(window, "_recent_loaded_generation", -1)
                != getattr(window, "_identity_context_generation", 0)
        ):
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
