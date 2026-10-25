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
    except Exception:
        window.diagnostics.add(
            "PLUGIN", "provider refresh after plugin change", "error",
            detail={"code": "provider_refresh_failed"}, level="detailed",
        )
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
    # On a user-initiated plugin change, an enabled public provider needs
    # a fresh session. Startup already schedules its own first connection.
    if getattr(window, "_source_inventory_startup_connection_seen", False):
        reconnect = getattr(window, "_connect_sources_if_open", None)
        if callable(reconnect):
            # The plugin reload callback is the audited consumer. Capture its
            # origin synchronously while the GTK coordinator submits work;
            # never leave it set for a later unrelated/manual reconnect.
            previous_caller = getattr(window, "_provider_connect_caller_hint", None)
            window._provider_connect_caller_hint = "plugin_activation"
            try:
                reconnect()
            finally:
                window._provider_connect_caller_hint = previous_caller


def subscribe_cached_book_details(window, *, idle_add, subscribe_cached_book_detail_changes) -> None:
    """Replace observers atomically; old or queued callbacks cannot refresh new data."""
    if getattr(window, "_closed", False):
        return
    # Invalidate the previous observer *before* calling a plugin-owned
    # unsubscriber: unsubscribe itself may dispatch a final notification.
    token = object()
    window._cached_book_subscription_token = token
    window._cached_book_unsubscribe()
    if getattr(window, "_closed", False) or window._cached_book_subscription_token is not token:
        return
    try:
        services = tuple(window.plugin_manager.services("book.detail.enrichment"))
    except Exception:
        services = ()
    if getattr(window, "_closed", False) or window._cached_book_subscription_token is not token:
        return
    window._cached_book_services = services

    def is_current():
        return (
            not getattr(window, "_closed", False)
            and getattr(window, "_cached_book_subscription_token", None) is token
        )

    def deliver():
        # An idle posted for the old plugin may execute *after* replacement.
        # Guard at GTK delivery time as well as at the producer callback.
        if not is_current():
            return False
        return window._cached_book_cache_invalidated()

    def invalidated():
        if is_current():
            idle_add(deliver)

    # Callbacks can arrive off the GTK thread and only schedule idle delivery.
    unsubscribe = subscribe_cached_book_detail_changes(services, invalidated)
    if not is_current():
        # A plugin may synchronously close the window while registering.
        # Do not keep a new observer alive after teardown.
        try:
            unsubscribe()
        except Exception:
            pass
        return
    window._cached_book_unsubscribe = unsubscribe
    window._cached_book_cache_invalidated()
