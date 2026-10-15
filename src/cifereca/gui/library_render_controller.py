"""Headless Library/Recent rendering orchestration (#1129).

GTK row factories, projections and date formatting are injected by MainWindow;
identity, cached snapshots, UI widgets and selection stay window-owned.
"""
from __future__ import annotations

from typing import Any


def _render_phase_probe(window, clock, category: str, started: float):
    """Opt-in, side-effect-free timing; no GTK imports or book identifiers.

    Sampling is disabled for ordinary renders and baseline comparisons so
    the collector cannot silently bias a 563-item list. Record only phases
    actually completed by this call; never pretend to measure a GTK frame.
    """
    monitor = getattr(window, "ui_perf", None)
    if getattr(monitor, "detailed_capture_enabled", False) is not True:
        return None
    record = getattr(monitor, "record_phase", None)
    if not callable(record):
        return None
    prior = started

    def mark(phase: str) -> None:
        nonlocal prior
        now = clock()
        try:
            record(category, phase, max(0.0, (now - prior) * 1000))
        except Exception:
            # A disabled/misbehaving monitor cannot break a GTK render.
            pass
        # Do not charge observer overhead to the next business phase.
        prior = clock()

    return mark


def render_library(window, entries: list[dict[str, Any]] | None = None, *, perf_counter, project_library_rows, BookListItem, fr_date) -> None:
    if not hasattr(window, "library_page"):
        return
    _perf_started = perf_counter()
    phase = _render_phase_probe(window, perf_counter, "library", _perf_started)
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
    if phase is not None:
        phase("row_projection")  # includes filtering and BookListItem creation
    window._attach_row_facets(rows, window._library_objects, window.library_controls)
    if phase is not None:
        phase("facets")
    window.library_page.show_rows(rows, total_count=len(entries))
    if phase is not None:
        phase("model_update")  # includes synchronous GTK ListStore signals
    restored_pending = window._select_pending_library_item()
    if not restored_pending and window.library_page.selection.get_selected_item() is None:
        window._library_selected(None)
    if phase is not None:
        phase("selection")
    window.ui_perf.record("render_library", (perf_counter() - _perf_started) * 1000.0)


def render_recent(window, items: list[dict[str, Any]] | None = None, *, perf_counter, project_recent_rows, BookListItem) -> None:
    if not hasattr(window, "recent_page"):
        return
    _perf_started = perf_counter()
    phase = _render_phase_probe(window, perf_counter, "recent", _perf_started)
    if items is None:
        items = list(window._recent_entries_cache)
    window._recent_entries_cache = list(items)
    rows, window._recent_objects = project_recent_rows(
        items, row_factory=BookListItem,
    )
    if phase is not None:
        phase("row_projection")
    window._attach_row_facets(rows, window._recent_objects, window.search_controls)
    if phase is not None:
        phase("facets")
    window.recent_page.show_rows(rows)
    if phase is not None:
        phase("model_update")
    window.ui_perf.record("render_recent", (perf_counter() - _perf_started) * 1000.0)
