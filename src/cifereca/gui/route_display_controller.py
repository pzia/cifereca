"""P0 #1130: GTK-neutral route presentation for MainWindow.

The host owns widgets, identity, cached entries and refresh services. Route
presentation never creates pages or triggers a source scan unless the original
MainWindow route was already responsible for a lazy refresh.
"""
from __future__ import annotations


def show_route(window, route: str, *, Gtk) -> None:
    if getattr(window, "_closed", False):
        return
    if route not in window._pages:
        return
    window.view_stack.set_visible_child_name(route)
    page = window._pages.get(route)
    window.header_subtitle.set_label(page.title if page else "")
    window._refresh_header_actions(route)
    widget = window._plugin_page_widgets.get(route)
    if widget and isinstance(widget.get_root(), Gtk.Window):
        widget.child_focus(Gtk.DirectionType.TAB_FORWARD)

    if route == "dashboard":
        window._render_dashboard()
    elif route == "library":
        window._render_library(window._library_entries_cache)
        if not window._library_entries_cache:
            window.refresh_library(False)
    elif route == "recent":
        window._render_recent(window._recent_entries_cache)
        if not window._recent_entries_cache:
            window.refresh_recent()
    elif route == "history" and not window.service.last_history:
        window.refresh_history(False)
    elif route == "bookings" and not window.service.last_bookings:
        window.refresh_bookings(False)
    elif route == "sync":
        window._refresh_sync_page()
    elif route == "tools":
        window._refresh_tools_page()
    elif route == "settings":
        window.refresh_diagnostics_text()
    elif route == "plugins" and hasattr(window.plugins_page, "refresh"):
        window.plugins_page.refresh()
