"""Identity activation UI coordinator (#1130).

No GTK dependency: MainWindow still owns identity, widgets, service, task
runner and navigation. The delayed callback is guarded against both window
teardown and identity generations; no new tasks or subscriptions are added.
"""
from __future__ import annotations

import logging

from ..identity_selection import identity_change_required
from ..task_queue import TaskPriority

_TRACE_LOG = logging.getLogger("cifereca")


def activate_identity(window, identity_id: str) -> None:
    if getattr(window, '_closed', False):
        return
    identity = window.service.identity_by_id(identity_id)
    if identity is None:
        raise ValueError("Identité inconnue")
    current = window.service.current_identity
    current_identity_id = current.identity_id if current is not None else None
    if not identity_change_required(current_identity_id, identity.identity_id):
        _TRACE_LOG.debug("Identity selection notification ignored: already current identity=%s", identity.identity_id)
        return
    window.service.select_identity(identity.identity_id)
    window._identity_context_generation += 1
    window._dispose_maintenance_lines()
    if window.view_stack.get_visible_child_name() == "tools":
        window._refresh_tools_page()
    window._account_context_generation += 1
    window._cached_book_request_generation += 1
    window._cached_book_context = None
    # Pending Library navigation belongs to the previous identity context.
    # Cancel it before rendering the new identity's (initially empty) list.
    window._pending_library_selection_key = None
    window._pending_library_selection_identity_id = None
    window._pending_library_selection_generation = None
    identity_generation = window._identity_context_generation
    window._populate_accounts()
    window._render_dashboard()
    window._search_generation = getattr(window, "_search_generation", 0) + 1
    window._search_detail_request_generation += 1
    window._search_local_objects = {}; window.search_objects = {}; window._remote_search_results = []
    window._loaded_search_context = None
    window._search_mode = "local"; window._remote_query_key = None; window._selected_search_id = None
    if hasattr(window, "search_page"):
        window.search_page.clear_rows()
        window.search_page.detail.empty("Nouvelle identité", "Lancez une recherche dans le connu.")
    # Do not rebuild the identity dropdown from its own notify::selected callback:
    # GTK may deliver the resulting notification asynchronously after the guard
    # is released, causing an unbounded refresh/task loop.
    window._populate_account_dropdown(identity.identity_id)
    window.service.last_history = []
    window.service.last_bookings = []
    window.history_objects = {}
    window.booking_objects = {}
    window._history_all_rows = []
    window._history_rows = []
    window._history_loaded_count = 0
    window._history_append_scheduled = False
    if hasattr(window, "history_page"):
        window.history_page.clear_rows()
    if hasattr(window, "booking_page"):
        window.booking_page.clear_rows()
    window._library_entries_cache = []
    window._recent_entries_cache = []
    window._recent_objects = {}
    if hasattr(window, "recent_page"):
        window.recent_page.clear_rows()
        window.recent_page.detail.empty("Nouvelle identité", "Chargement des récupérations…")
    if hasattr(window, "library_page"):
        window._render_library([])
        window.library_page.detail.empty("Nouvelle identité", "Chargement de la bibliothèque…")
    def identity_loaded(rows: list[Loan]) -> None:
        if getattr(window, '_closed', False):
            return
        if not window.service.current_identity or window.service.current_identity.identity_id != identity.identity_id:
            return
        if not window._identity_request_current(identity.identity_id, identity_generation):
            return
        window._populate_loans(rows)
        window.refresh_library(False)
        window.refresh_recent()
        route = window.view_stack.get_visible_child_name() if hasattr(window, "view_stack") else None
        if route == "history":
            window.refresh_history(False)
        elif route == "bookings":
            window.refresh_bookings(False)
        window._activity_result(f"Identité chargée : {identity.label}")
    window.runner.submit(
        f"Chargement de {identity.label}…",
        lambda: window.service.refresh_identity_loans(force=False, identity_id=identity.identity_id),
        identity_loaded,
        priority=TaskPriority.USER_ACTION,
        kind="identity",
    )
