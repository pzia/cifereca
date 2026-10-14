"""CoreUI #1129: progressive History and Bookings page result projection.

Services, account/identity generations and page widgets are still owned by
MainWindow. No GTK/PyGObject import or provider request is added; GLib,
presentation and row factories are injected through the existing facades.
"""
from __future__ import annotations

from . import history_language_projection


def populate_history(window, rows, preserve_all: bool = False) -> None:
    if getattr(window, "_closed", False):
        return
    if not preserve_all:
        window._history_all_rows = list(rows)
    wanted_languages = (
        set(window.history_languages.values())
        if hasattr(window, "history_languages") else set()
    )
    if wanted_languages:
        # Preserve #1624's single local catalogue read for the whole batch.
        catalog_rows = window.service.storage.catalog_entries() if rows else ()
        rows = history_language_projection.history_rows_with_languages(
            rows, wanted_languages, catalog_rows,
        )
    window.history_objects = {item.id: item for item in rows}
    window._history_rows = list(rows)
    window._history_loaded_count = 0
    window._history_append_scheduled = False
    initial = window._history_rows[:window._history_page_size]
    window._history_loaded_count = len(initial)
    window.history_page.show_rows(
        [window._history_book_row(item) for item in initial],
        total_count=len(window._history_rows),
    )
    if len(window._history_rows) > window._history_loaded_count:
        window.diagnostics.add(
            "UI", "history progressive load", "ok",
            detail={
                "loaded": window._history_loaded_count,
                "total": len(window._history_rows),
                "page_size": window._history_page_size,
            },
            level="detailed",
        )


def history_scroll_changed(window, adjustment, *, GLib) -> None:
    if getattr(window, "_closed", False):
        return
    if (
        window._history_loaded_count >= len(window._history_rows)
        or window._history_append_scheduled
    ):
        return
    remaining = adjustment.get_upper() - (
        adjustment.get_value() + adjustment.get_page_size()
    )
    threshold = max(420.0, adjustment.get_page_size() * 0.75)
    if remaining <= threshold:
        window._history_append_scheduled = True
        GLib.idle_add(window._append_history_chunk)


def append_history_chunk(window) -> bool:
    window._history_append_scheduled = False
    # A queued GTK idle may outlive the window and its History widgets.
    if getattr(window, "_closed", False):
        return False
    if window._history_loaded_count >= len(window._history_rows):
        return False
    start = window._history_loaded_count
    end = min(len(window._history_rows), start + window._history_page_size)
    chunk = window._history_rows[start:end]
    window._history_loaded_count = end
    window.history_page.append_rows(
        [window._history_book_row(item) for item in chunk],
        total_count=len(window._history_rows),
    )
    window.diagnostics.add(
        "UI", "history progressive load", "ok",
        detail={
            "loaded": window._history_loaded_count,
            "total": len(window._history_rows),
            "page_size": window._history_page_size,
        },
        level="detailed",
    )
    return False


def populate_bookings(
    window, rows, *, BookListItem, booking_presentation, fr_date,
) -> None:
    if getattr(window, "_closed", False):
        return
    window.booking_objects = {item.id: item for item in rows}
    ui_rows = []
    for item in rows:
        presentation = booking_presentation(item)
        ui_rows.append(
            BookListItem(
                item.id,
                item.title,
                item.location or "",
                f"Réservé le {fr_date(item.booking_date)}",
                presentation.status_label,
                None,
            )
        )
    objects = {
        item.id: {"source_provider": item.source_provider}
        for item in rows
    }
    window._attach_row_facets(ui_rows, objects, window.search_controls)
    window.booking_page.show_rows(ui_rows)
