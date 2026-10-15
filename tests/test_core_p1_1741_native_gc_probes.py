"""P1 #1741: opt-in GC timing of native GTK model updates, without GI.

The production controller is executed with a GTK-shaped page: get_root()
proves that the native update seam is present. GC callback measurements are
real Python collections, but the catalogue and GUI are simulated.
"""
from __future__ import annotations

import gc
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.ui_perf import UiPerfMonitor
from cifereca.gui.library_render_controller import render_recent
from tests.test_core_p1_1741_library_render_phases import _clock, _library_context


def test_gc_probe_installed_only_during_explicit_capture():
    monitor = UiPerfMonitor()
    initial_callbacks = tuple(gc.callbacks)
    with monitor.observe_gc("library"):
        gc.collect(2)
    assert tuple(gc.callbacks) == initial_callbacks
    assert monitor.performance_report()["no_samples"] is True
    monitor.enable_detailed_capture(True)
    with monitor.observe_gc("library"):
        gc.collect(2)
        report = monitor.performance_report()
        # The GC callback queues samples locally; no metric is materialized
        # until after leaving the native GTK operation.
        assert report["no_samples"] is True
    result = monitor.performance_report()
    assert result["stats"]["library.gc_gen2"]["count"] == 1
    assert result["stats"]["library.gc_gen2"]["p50_ms"] >= 0
    assert result["measurement_origin"] == "observed"
    assert [event["phase"] for event in result["events"]] == ["gc_gen2"]
    assert result["events"][0]["at_ms"] >= 0
    assert tuple(gc.callbacks) == initial_callbacks


def test_gc_probe_is_removed_when_the_business_operation_fails():
    monitor = UiPerfMonitor(measurement_origin="simulated")
    monitor.enable_detailed_capture(True)
    original = tuple(gc.callbacks)
    with pytest.raises(RuntimeError, match="business failure"):
        with monitor.observe_gc("library"):
            gc.collect(2)
            raise RuntimeError("business failure")
    assert tuple(gc.callbacks) == original
    assert monitor.performance_report()["stats"]["library.gc_gen2"]["count"] == 1


def test_nested_same_category_probe_counts_one_gc_pause():
    monitor = UiPerfMonitor()
    monitor.enable_detailed_capture(True)
    with monitor.observe_gc("library"):
        with monitor.observe_gc("library"):
            gc.collect(0)
    result = monitor.performance_report()
    assert result["stats"]["library.gc_gen0"]["count"] == 1
    assert result["gc_samples_dropped"] == 0


def test_gc_samples_are_bounded_before_flushing():
    monitor = UiPerfMonitor(max_samples=20, max_events=30)
    monitor.enable_detailed_capture(True)
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        with monitor.observe_gc("library"):
            for _ in range(35):
                gc.collect(0)
    finally:
        if was_enabled:
            gc.enable()
    report = monitor.performance_report()
    assert report["stats"]["library.gc_gen0"]["count"] == 20
    assert report["gc_samples_dropped"] == 15
    assert len(report["events"]) == 20


def test_unknown_category_redacted_and_stopping_capture_ends_collection():
    monitor = UiPerfMonitor()
    monitor.enable_detailed_capture(True)
    private = "https://secret.invalid/?token=PRIVATE_TOKEN"
    with monitor.observe_gc(private):
        gc.collect(2)
        monitor.enable_detailed_capture(False)
        gc.collect(2)
    report = monitor.performance_report()
    assert private not in str(report)
    assert "PRIVATE_TOKEN" not in str(report)
    assert report["stats"]["other.gc_gen2"]["count"] == 1
    assert report["detailed_capture_enabled"] is False


def test_library_native_update_records_real_gc_without_extra_rows():
    monitor = UiPerfMonitor(measurement_origin="simulated")
    monitor.enable_detailed_capture(True)
    window, _entries, events, render = _library_context(monitor)

    def show(rows, *, total_count):
        events.append(("show", len(rows), total_count))
        gc.collect(2)

    window.library_page.show_rows = show
    window.library_page.get_root = lambda: object()  # GTK-shaped native page
    render(_clock())
    report = monitor.performance_report()
    assert report["stats"]["library.gc_gen2"]["count"] == 1
    phases = [row["phase"] for row in report["events"]]
    assert phases == [
        "row_projection", "facets", "gc_gen2", "model_update", "selection",
    ]
    assert events.count(("show", 2, 2)) == 1
    assert report["measurement_origin"] == "simulated"


def test_recent_native_update_is_separate_and_disabled_mode_does_not_profile():
    monitor = UiPerfMonitor(measurement_origin="simulated")
    operations = []
    def show(rows):
        operations.append(("show", len(rows)))
        gc.collect(2)

    page = NS(get_root=lambda: object(), show_rows=show)
    host = NS(
        ui_perf=monitor, recent_page=page, _recent_entries_cache=[],
        search_controls=object(),
        _attach_row_facets=lambda *a: operations.append(("facets",)),
    )
    def project(rows, **_kw):
        operations.append(("project",))
        return rows, {}

    for capture in (False, True):
        if capture:
            monitor.enable_detailed_capture(True)
        render_recent(host, [{"key": "private"}], perf_counter=_clock(),
                      project_recent_rows=project, BookListItem=object)
    report = monitor.performance_report()
    assert report["stats"]["recent.gc_gen2"]["count"] == 1
    assert [row["phase"] for row in report["events"]] == [
        "row_projection", "facets", "gc_gen2", "model_update",
    ]
    assert operations.count(("show", 1)) == 2
