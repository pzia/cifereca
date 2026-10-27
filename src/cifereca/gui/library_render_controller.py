"""Headless Library/Recent rendering orchestration (#1129).

GTK row factories, projections and date formatting are injected by MainWindow;
identity, cached snapshots, UI widgets and selection stay window-owned.
"""
from __future__ import annotations

from typing import Any

from .gtk_frame_paint_probe import GtkFramePaintProbe


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
    generation = getattr(monitor, "capture_generation", None)
    try:
        allocator = getattr(monitor, "new_operation_id", None)
        operation_id = allocator() if callable(allocator) else None
    except Exception:
        operation_id = None
    prior = started

    def is_current() -> bool:
        return (
            getattr(monitor, "detailed_capture_enabled", False) is True
            and getattr(monitor, "capture_generation", None) == generation
        )

    def mark(phase: str) -> None:
        nonlocal prior
        if not is_current():
            return
        now = clock()
        try:
            record(
                category, phase, max(0.0, (now - prior) * 1000),
                operation_id=operation_id, trigger="navigation",
                capture_generation=generation,
            )
        except Exception:
            # A disabled/misbehaving monitor cannot break a GTK render.
            pass
        # Do not charge observer overhead to the next business phase.
        prior = clock()

    mark.operation_id = operation_id
    mark.is_current = is_current
    return mark



def _observe_after_paint(
    window, category: str, widget, clock, operation_id: int | None = None,
) -> None:
    """Only for mapped GTK widgets during explicit detailed capture.

    GDK after-paint is the end of frame-clock processing, NOT a guarantee
    that pixels were presented to the display. Missing frames stay absent.
    """
    if getattr(window, "_closed", False):
        return
    monitor = getattr(window, "ui_perf", None)
    if getattr(monitor, "detailed_capture_enabled", False) is not True:
        return
    if not callable(getattr(widget, "get_mapped", None)):
        return
    try:
        probe = getattr(window, "_ui_perf_frame_probe", None)
        if probe is None:
            # GTK and GLib are only imported on opt-in user capture.
            from gi.repository import GLib
            probe = GtkFramePaintProbe(
                timeout_add=GLib.timeout_add,
                source_remove=GLib.source_remove,
            )
            window._ui_perf_frame_probe = probe
        probe.observe(category, widget, monitor, clock=clock, operation_id=operation_id)
    except Exception:
        # Observability must never change the business render outcome.
        pass


def render_library(window, entries: list[dict[str, Any]] | None = None, *, perf_counter, project_library_rows, BookListItem, fr_date) -> None:
    if not hasattr(window, "library_page"):
        return
    _perf_started = perf_counter()
    phase = _render_phase_probe(window, perf_counter, "library", _perf_started)
    identity_id = window.service.current_identity.identity_id if window.service.current_identity else None
    if entries is None:
        entries = list(window._library_entries_cache)
    window._library_entries_cache = list(entries)
    library_search_state = window.library_controls.state()
    rows, window._library_objects = project_library_rows(
        library_search_state.filter(entries),
        row_factory=BookListItem,
        provider_display_name=window.service.provider_display_name,
        availability_label=window._library_availability_label,
        backup_status_label=window._kobo_backup_status_label,
        format_date=fr_date,
        query=getattr(library_search_state, "text", ""),
        field=getattr(library_search_state, "field", "all"),
    )
    if phase is not None:
        phase("row_projection")  # includes filtering and BookListItem creation
    window._attach_row_facets(rows, window._library_objects, window.library_controls)
    if phase is not None:
        phase("facets")
    # GC observation is opt-in and only surrounds native GTK model updates.
    # No hook is installed for headless adapters or ordinary rendering.
    monitor = getattr(window, "ui_perf", None)
    gc_probe = (
        getattr(monitor, "observe_gc", None)
        if phase is not None and phase.is_current() else None
    )
    if callable(gc_probe) and callable(getattr(window.library_page, "get_root", None)):
        with gc_probe("library"):
            window.library_page.show_rows(rows, total_count=len(entries))
    else:
        window.library_page.show_rows(rows, total_count=len(entries))
    if phase is not None:
        phase("model_update")  # includes synchronous GTK ListStore signals
    restored_pending = window._select_pending_library_item()
    if not restored_pending and window.library_page.selection.get_selected_item() is None:
        window._library_selected(None)
    if phase is not None:
        phase("selection")
    window.ui_perf.record("render_library", (perf_counter() - _perf_started) * 1000.0)
    if phase is not None and phase.is_current():
        _observe_after_paint(
            window, "library", window.library_page, perf_counter,
            operation_id=getattr(phase, "operation_id", None),
        )


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
    monitor = getattr(window, "ui_perf", None)
    gc_probe = (
        getattr(monitor, "observe_gc", None)
        if phase is not None and phase.is_current() else None
    )
    if callable(gc_probe) and callable(getattr(window.recent_page, "get_root", None)):
        with gc_probe("recent"):
            window.recent_page.show_rows(rows)
    else:
        window.recent_page.show_rows(rows)
    if phase is not None:
        phase("model_update")
    window.ui_perf.record("render_recent", (perf_counter() - _perf_started) * 1000.0)
    if phase is not None and phase.is_current():
        _observe_after_paint(
            window, "recent", window.recent_page, perf_counter,
            operation_id=getattr(phase, "operation_id", None),
        )
