"""P1 #1741: real, opt-in Library/Recents phase timings without GTK dependencies."""
from __future__ import annotations

from itertools import count
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.library_render_controller import render_library, render_recent
from cifereca.gui.ui_perf import UiPerfMonitor


def _clock(start=0, step=0.01):
    ticks = count()
    return lambda: start + next(ticks) * step


def _library_context(monitor):
    events = []
    entries = [{"id": "private-1"}, {"id": "private-2"}]
    controls = NS(
        state=lambda: NS(filter=lambda values: (events.append("filter") or values)),
    )
    page = NS(
        show_rows=lambda rows, *, total_count: events.append(
            ("show", len(rows), total_count)
        ),
        selection=NS(get_selected_item=lambda: None),
    )
    window = NS(
        ui_perf=monitor,
        service=NS(current_identity=NS(identity_id="private-id"),
                   provider_display_name=lambda pid: pid),
        library_page=page, library_controls=controls,
        _library_entries_cache=[],
        _library_availability_label=Mock(),
        _kobo_backup_status_label=Mock(),
        _attach_row_facets=lambda rows, mapping, state: events.append("facets"),
        _select_pending_library_item=lambda: events.append("pending") or False,
        _library_selected=lambda row: events.append("selection"),
    )
    def project(source, **_kwargs):
        events.append("project")
        return list(source), {"private-book": object()}

    def call(clock):
        render_library(
            window, entries, perf_counter=clock,
            project_library_rows=project, BookListItem=object,
            fr_date=lambda v: str(v),
        )
    return window, entries, events, call


def test_default_library_render_stays_unprobed_and_uses_original_clock_budget():
    monitor = UiPerfMonitor(measurement_origin="simulated")
    _window, _entries, events, call = _library_context(monitor)
    # Ordinary rendering consumes precisely the two legacy clock reads.
    timestamps = iter((10.0, 10.012))
    call(lambda: next(timestamps))
    assert events == [
        "filter", "project", "facets", ("show", 2, 2), "pending", "selection",
    ]
    assert monitor.snapshot()["render_library"]["count"] == 1
    assert monitor.snapshot()["render_library"]["p50_ms"] == 12
    assert monitor.performance_report()["no_samples"] is True
    assert monitor.performance_report()["events"] == []


def test_explicit_capture_measures_four_completed_library_phases():
    monitor = UiPerfMonitor(measurement_origin="simulated")
    monitor.enable_detailed_capture(True)
    _window, _entries, events, call = _library_context(monitor)
    call(_clock())
    report = monitor.performance_report()
    assert report["measurement_origin"] == "simulated"
    assert [event["phase"] for event in report["events"]] == [
        "row_projection", "facets", "model_update", "selection",
    ]
    assert {event["category"] for event in report["events"]} == {"library"}
    assert {event["outcome"] for event in report["events"]} == {"unknown"}
    assert all(event["elapsed_ms"] == pytest.approx(10) for event in report["events"])
    assert report["stats"]["library.row_projection"]["count"] == 1
    assert report["stats"]["library.model_update"]["p95_ms"] == 10
    assert monitor.snapshot()["render_library"]["count"] == 1
    assert "private-book" not in str(report)
    assert "private-id" not in str(report)
    assert events == [
        "filter", "project", "facets", ("show", 2, 2), "pending", "selection",
    ]


def test_recent_is_similarly_opt_in_and_never_mislabels_frames():
    monitor = UiPerfMonitor(measurement_origin="simulated")
    monitor.enable_detailed_capture(True)
    events = []
    window = NS(
        ui_perf=monitor,
        recent_page=NS(show_rows=lambda rows: events.append(("show", len(rows)))),
        _recent_entries_cache=[],
        search_controls=object(),
        _attach_row_facets=lambda rows, mapping, controls: events.append("facets"),
    )
    rows = [{"id": "private-recent"}]
    def projection(items, **_kwargs):
        events.append("project")
        return items, {"private-recent": object()}

    render_recent(
        window, rows, perf_counter=_clock(10),
        project_recent_rows=projection, BookListItem=object,
    )
    assert events == ["project", "facets", ("show", 1)]
    report = monitor.performance_report()
    assert [event["phase"] for event in report["events"]] == [
        "row_projection", "facets", "model_update",
    ]
    assert {event["category"] for event in report["events"]} == {"recent"}
    assert "recent.frame_wait" not in report["stats"]
    assert report["stats"]["recent.model_update"]["count"] == 1
    assert monitor.snapshot()["render_recent"]["count"] == 1


def test_monitor_failure_must_not_break_business_render_or_legacy_measurement():
    events = []
    monitor = NS(
        detailed_capture_enabled=True,
        record_phase=lambda *_a: (_ for _ in ()).throw(RuntimeError("observer failed")),
        record=lambda name, duration: events.append(("legacy", name, duration)),
    )
    _, _, progress, call = _library_context(monitor)
    call(_clock())
    assert progress == [
        "filter", "project", "facets", ("show", 2, 2), "pending", "selection",
    ]
    assert len(events) == 1
    assert events[0][1] == "render_library"
    assert events[0][2] >= 0


def test_missing_page_never_touches_clock_or_begins_capture():
    clock = Mock()
    monitor = UiPerfMonitor()
    monitor.enable_detailed_capture(True)
    render_library(NS(ui_perf=monitor), [], perf_counter=clock,
                   project_library_rows=Mock(), BookListItem=object, fr_date=str)
    render_recent(NS(ui_perf=monitor), [], perf_counter=clock,
                  project_recent_rows=Mock(), BookListItem=object)
    clock.assert_not_called()
    assert monitor.performance_report()["no_samples"] is True
