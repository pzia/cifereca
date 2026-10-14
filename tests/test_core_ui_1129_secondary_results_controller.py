"""CoreUI P0 #1129: History pagination and Bookings projections are headless."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.secondary_results_controller import (
    populate_history, history_scroll_changed, append_history_chunk,
    populate_bookings,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "secondary_results_controller.py"


def _item(index, *, language="fr"):
    return NS(
        id=f"item-{index}",
        source_provider="paris",
        record_id=f"record-{index}",
        language=language,
    )


def _host(*, page_size=2, history_rows=()):
    events = []
    runner = []
    def log(name):
        return lambda rows, **kwargs: events.append(
            (name, list(rows), kwargs.get("total_count"))
        )
    host = NS(
        _closed=False,
        _history_page_size=page_size,
        _history_all_rows=["private-old"],
        _history_rows=["private-old"],
        _history_loaded_count=1,
        _history_append_scheduled=False,
        history_objects={"old": "private"},
        booking_objects={"old": "booking"},
        history_page=NS(
            show_rows=log("history-show"),
            append_rows=log("history-append"),
        ),
        booking_page=NS(show_rows=log("booking-show")),
        history_languages=NS(values=lambda: []),
        _known_item_languages=lambda _provider, item: (
            events.append(("language", item)) or {"fr"}
        ),
        _history_book_row=lambda item: NS(key=item.id),
        diagnostics=NS(add=lambda *args, **kw:
                       events.append(("diagnostic", args, kw))),
        search_controls=object(),
        _attach_row_facets=lambda rows, mapping, controls:
            events.append(("facets", rows, mapping, controls)),
    )
    host._test_catalog_rows = []
    host._test_catalog_calls = []
    host.service = NS(storage=NS(catalog_entries=lambda: (
        host._test_catalog_calls.append("catalog") or list(host._test_catalog_rows)
    )))
    host._append_history_chunk = lambda: append_history_chunk(host)
    glib = NS(idle_add=lambda callback: runner.append(callback))
    return host, events, runner, glib


def _scroll(*, upper=1000, value=0, page=100):
    return NS(
        get_upper=lambda: upper,
        get_value=lambda: value,
        get_page_size=lambda: page,
    )


def _bookings(host, items):
    def fake_list_item(*args):
        return NS(args=args)
    return populate_bookings(
        host, items,
        BookListItem=fake_list_item,
        booking_presentation=lambda booking: NS(
            status_label=f"Présentation {booking.state}",
        ),
        fr_date=lambda value: f"FR-{value}",
    )


def test_controller_imports_without_gtk_and_preserves_four_window_facades():
    module = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(module)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in item or "main_window" in item for item in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in main.body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    methods = {node.name: node for node in cls.body
               if isinstance(node, ast.FunctionDef)}
    for facade, controller, args in (
        ("_populate_history", "populate_history", ("rows", "preserve_all")),
        ("_history_scroll_changed", "history_scroll_changed", ("adjustment",)),
        ("_append_history_chunk", "append_history_chunk", ()),
        ("_populate_bookings", "populate_bookings", ("rows",)),
    ):
        method = methods[facade]
        assert tuple(arg.arg for arg in method.args.args) == ("self", *args)
        assert f"secondary_results_controller.{controller}" in ast.unparse(method)
    assert [arg.arg for arg in methods["_populate_history"].args.args] == [
        "self", "rows", "preserve_all",
    ]


def test_initial_history_projection_preserves_business_total_over_loaded_chunk():
    host, events, callbacks, glib = _host(page_size=2)
    data = [_item(i) for i in range(5)]
    populate_history(host, data)
    assert host._history_all_rows == data
    assert host._history_rows == data
    assert host._history_loaded_count == 2
    assert set(host.history_objects) == {item.id for item in data}
    assert events[0][0] == "history-show"
    assert [row.key for row in events[0][1]] == ["item-0", "item-1"]
    assert events[0][2] == 5
    assert events[1] == (
        "diagnostic", ("UI", "history progressive load", "ok"),
        {"detail": {"loaded": 2, "total": 5, "page_size": 2}, "level": "detailed"},
    )


def test_small_history_page_renders_all_without_progress_diagnostic():
    host, events, callbacks, glib = _host(page_size=5)
    data = [_item(i) for i in range(3)]
    populate_history(host, data)
    assert events == [("history-show", [NS(key="item-0"), NS(key="item-1"), NS(key="item-2")], 3)]


def test_history_empty_results_clear_previous_private_rows_and_objects():
    host, events, callbacks, glib = _host()
    populate_history(host, [])
    assert host.history_objects == {}
    assert host._history_all_rows == []
    assert host._history_rows == []
    assert host._history_loaded_count == 0
    assert events == [("history-show", [], 0)]


def test_language_filter_never_changes_full_unfiltered_history_cache():
    host, events, callbacks, glib = _host(page_size=1)
    source = [_item(0, language="fr"), _item(1, language="de"), _item(2, language="fr")]
    host.history_languages.values = lambda: ["fr"]
    host._test_catalog_rows = [
        {"provider_id": "paris", "item_id": f"record-{i}",
         "metadata": {"language": "de" if i == 1 else "fr"}}
        for i in range(3)
    ]
    populate_history(host, source)
    assert host._history_all_rows == source
    assert [item.id for item in host._history_rows] == ["item-0", "item-2"]
    assert events[0][2] == 2
    host.history_languages.values = lambda: ["de"]
    populate_history(host, list(host._history_all_rows), preserve_all=True)
    assert host._history_all_rows == source
    assert [item.id for item in host._history_rows] == ["item-1"]
    assert events[-1][0] == "history-show"
    assert events[-1][2] == 1


def test_history_does_not_resolve_languages_when_no_filter_is_selected():
    host, events, callbacks, glib = _host()
    host._known_item_languages = lambda *_a: (_ for _ in ()).throw(
        AssertionError("no catalog language read expected")
    )
    populate_history(host, [_item(0)])
    assert host._history_loaded_count == 1


def test_scroll_queues_one_idle_only_when_close_to_end_of_visible_list():
    host, events, callbacks, glib = _host()
    populate_history(host, [_item(i) for i in range(5)])
    history_scroll_changed(host, _scroll(upper=1600, value=0, page=100), GLib=glib)
    assert callbacks == []
    history_scroll_changed(host, _scroll(upper=1000, value=500, page=100), GLib=glib)
    assert len(callbacks) == 1
    assert host._history_append_scheduled is True
    history_scroll_changed(host, _scroll(upper=1000, value=600, page=100), GLib=glib)
    assert len(callbacks) == 1


def test_large_visible_window_scales_scroll_threshold_without_false_positive():
    host, events, callbacks, glib = _host()
    populate_history(host, [_item(i) for i in range(6)])
    history_scroll_changed(
        host, _scroll(upper=2000, value=0, page=1000), GLib=glib,
    )
    assert callbacks == []
    history_scroll_changed(
        host, _scroll(upper=2000, value=300, page=1000), GLib=glib,
    )
    assert len(callbacks) == 1


def test_append_chunks_never_changes_backend_total_and_returns_false():
    host, events, callbacks, glib = _host(page_size=2)
    populate_history(host, [_item(i) for i in range(5)])
    history_scroll_changed(host, _scroll(upper=1000, value=700, page=100), GLib=glib)
    assert callbacks[0]() is False
    assert host._history_loaded_count == 4
    assert host._history_append_scheduled is False
    assert events[-2][0] == "history-append"
    assert [row.key for row in events[-2][1]] == ["item-2", "item-3"]
    assert events[-2][2] == 5
    assert events[-1][2]["detail"] == {"loaded": 4, "total": 5, "page_size": 2}
    assert append_history_chunk(host) is False
    assert host._history_loaded_count == 5
    assert [row.key for row in events[-2][1]] == ["item-4"]
    assert events[-2][2] == 5


def test_append_no_more_rows_does_not_add_duplicate_widget_rows():
    host, events, callbacks, glib = _host(page_size=3)
    populate_history(host, [_item(i) for i in range(2)])
    events.clear()
    assert append_history_chunk(host) is False
    assert events == []


def test_pending_idle_after_identity_clears_history_cannot_restore_private_data():
    host, events, callbacks, glib = _host()
    populate_history(host, [_item(i) for i in range(6)])
    history_scroll_changed(host, _scroll(upper=1000, value=800, page=100), GLib=glib)
    assert len(callbacks) == 1
    populate_history(host, [])
    events.clear()
    assert callbacks[0]() is False
    assert events == []
    assert host._history_loaded_count == 0


def test_closed_window_does_not_append_or_render_history():
    host, events, callbacks, glib = _host()
    populate_history(host, [_item(i) for i in range(4)])
    host._closed = True
    events.clear()
    history_scroll_changed(host, _scroll(upper=1, value=0, page=1), GLib=glib)
    assert callbacks == []
    assert append_history_chunk(host) is False
    populate_history(host, [_item(77)])
    assert events == []
    assert host._history_append_scheduled is False


def test_bookings_projection_keeps_exact_source_provenance_and_business_status():
    host, events, callbacks, glib = _host()
    items = [
        NS(id="a", title="Réservation A", location=None, booking_date="2026-10-14",
           state="ready", source_provider="paris"),
        NS(id="b", title="Réservation B", location="Site B", booking_date="2026-10-15",
           state="active", source_provider="other"),
    ]
    _bookings(host, items)
    assert host.booking_objects == {"a": items[0], "b": items[1]}
    assert events[0][0] == "facets"
    rows, origins, controls = events[0][1:]
    assert [row.args for row in rows] == [
        ("a", "Réservation A", "", "Réservé le FR-2026-10-14", "Présentation ready", None),
        ("b", "Réservation B", "Site B", "Réservé le FR-2026-10-15", "Présentation active", None),
    ]
    assert origins == {"a": {"source_provider": "paris"}, "b": {"source_provider": "other"}}
    assert controls is host.search_controls
    assert events[1][0] == "booking-show"
    assert events[1][1] == rows


def test_empty_booking_response_clears_private_booking_objects_and_page():
    host, events, callbacks, glib = _host()
    _bookings(host, [])
    assert host.booking_objects == {}
    assert events[0][0] == "facets"
    assert events[1] == ("booking-show", [], None)


def test_late_booking_projection_after_teardown_is_a_noop():
    host, events, callbacks, glib = _host()
    host._closed = True
    _bookings(host, [NS(id="private")])
    assert events == []
    assert host.booking_objects == {"old": "booking"}


def test_no_history_append_pending_when_last_row_is_already_loaded():
    host, events, callbacks, glib = _host(page_size=9)
    populate_history(host, [_item(0)])
    history_scroll_changed(host, _scroll(upper=100, value=100, page=100), GLib=glib)
    assert callbacks == []


def test_scroll_threshold_uses_last_visible_edge_and_not_absolute_offset():
    host, events, callbacks, glib = _host()
    populate_history(host, [_item(i) for i in range(5)])
    history_scroll_changed(host, _scroll(upper=1000, value=450, page=100), GLib=glib)
    assert callbacks == []
    history_scroll_changed(host, _scroll(upper=1000, value=500, page=100), GLib=glib)
    assert len(callbacks) == 1


def test_filtered_history_reads_catalog_once_per_batch_not_per_row():
    host, events, callbacks, glib = _host(page_size=3)
    host.history_languages.values = lambda: ["fr"]
    host._test_catalog_rows = [
        {"provider_id": "paris", "item_id": f"record-{i}",
         "metadata": {"language": "fr"}}
        for i in range(120)
    ]
    populate_history(host, [_item(i) for i in range(120)])
    assert host._test_catalog_calls == ["catalog"]
    assert host._history_loaded_count == 3
    assert len(host._history_rows) == 120
