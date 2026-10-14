"""Headless Library/Recent rendering orchestration (#1129).

GTK row factories, projections and date formatting are injected by MainWindow;
identity, cached snapshots, UI widgets and selection stay window-owned.
"""
from __future__ import annotations

from typing import Any


def render_library(window, entries: list[dict[str, Any]] | None = None, *, perf_counter, project_library_rows, BookListItem, fr_date) -> None:
    if not hasattr(window, "library_page"):
        return
    _perf_started = perf_counter()
    identity_id = window.service.current_identity.identity_id if window.service.current_identity else None
    if entries is None:
        entries = list(window._library_entries_cache)
    window._library_entries_cache = list(entries)
    rows, window._library_objects = project_library_rows(
        window.library_controls.state().filter(entries),
        row_factory=BookListItem,
        provider_display_name=window.service.provider_display_name,
        availability_label=window._library_availability_label,
        backup_status_label=window._kobo_backup_status_label,
        format_date=fr_date,
    )
    window._attach_row_facets(rows, window._library_objects, window.library_controls)
    window.library_page.show_rows(rows, total_count=len(entries))
    restored_pending = window._select_pending_library_item()
    if not restored_pending and window.library_page.selection.get_selected_item() is None:
        window._library_selected(None)
    window.ui_perf.record("render_library", (perf_counter() - _perf_started) * 1000.0)


def render_recent(window, items: list[dict[str, Any]] | None = None, *, perf_counter, project_recent_rows, BookListItem) -> None:
    if not hasattr(window, "recent_page"):
        return
    _perf_started = perf_counter()
    if items is None:
        items = list(window._recent_entries_cache)
    window._recent_entries_cache = list(items)
    rows, window._recent_objects = project_recent_rows(
        items, row_factory=BookListItem,
    )
    window._attach_row_facets(rows, window._recent_objects, window.search_controls)
    window.recent_page.show_rows(rows)
    window.ui_perf.record("render_recent", (perf_counter() - _perf_started) * 1000.0)
