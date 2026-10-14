"""Plugin shell refresh and detail-cache subscriptions, independent of GTK (#1130).

MainWindow retains lifetime, widget, plugin manager and identity ownership.
GLib dispatch and subscription helpers are supplied by the public facade.
"""
from __future__ import annotations


def plugins_changed(window) -> None:
    # Plugin-manager callbacks may outlive the MainWindow that registered
    # them. Never reconstruct detached GTK shell widgets after teardown.
    if getattr(window, "_closed", False):
        return
    if window.plugin_manager is None:
        return
    try:
        window.service.attach_plugin_manager(window.plugin_manager)
    except Exception as exc:
        window.diagnostics.add("PLUGIN", "provider refresh after plugin change", "error", detail={"error": str(exc)}, level="detailed")
    # A plugin hook may synchronously dispose the window while attaching or
    # rebuilding its pages. Do not resume touching detached GTK widgets.
    if getattr(window, "_closed", False):
        return
    old = window.shell_header
    window.shell_root.remove(old)
    window.shell_header = window._create_shell_header()
    if getattr(window, "_closed", False):
        return
    window.shell_root.prepend(window.shell_header)
    window._ensure_pages_menu_rightmost()
    window._rebuild_plugin_pages()
    if getattr(window, "_closed", False):
        return
    window._populate_accounts()
    if getattr(window, "_closed", False):
        return
    window._subscribe_cached_book_details()


def subscribe_cached_book_details(window, *, idle_add, subscribe_cached_book_detail_changes) -> None:
    """Refresh the selected book on cache invalidation, not the full list."""
    if getattr(window, "_closed", False):
        return
    window._cached_book_unsubscribe()
    # Unsubscribing may synchronously trigger teardown; never bind a new
    # callback to a disposed MainWindow.
    if getattr(window, "_closed", False):
        return
    try:
        services = tuple(window.plugin_manager.services("book.detail.enrichment"))
    except Exception:
        services = ()
    if getattr(window, "_closed", False):
        return
    window._cached_book_services = services
    # Plugin callbacks may run on any thread; GTK must only be touched in
    # an idle callback. These subscriptions are replaced on plugin changes.
    window._cached_book_unsubscribe = subscribe_cached_book_detail_changes(
        services, lambda: None if window._closed else idle_add(window._cached_book_cache_invalidated),
    )
    window._cached_book_cache_invalidated()
