"""Old render/GC probes cannot publish into a restarted performance capture."""
import gc
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.ui_perf import UiPerfMonitor
from cifereca.gui.library_render_controller import render_recent, _render_phase_probe
from tests.test_core_p1_1741_gtk_frame_phase_correlation import mounted, clock
from tests.test_core_p1_1741_gtk_frame_paint_probe import rig


@pytest.fixture(autouse=True)
def explicit_collections_only():
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


def restart(monitor):
    monitor.enable_detailed_capture(False)
    monitor.enable_detailed_capture(True)


def test_buffered_gc_from_old_capture_never_enters_new_timeline():
    monitor = UiPerfMonitor()
    monitor.enable_detailed_capture(True)
    callbacks = tuple(gc.callbacks)
    with monitor.observe_gc("library"):
        gc.collect(2)
        restart(monitor)
    report = monitor.performance_report()
    assert report["events"] == []
    # Passive statistics intentionally span captures; only the detailed
    # timeline belongs to the most recent capture.
    assert report["stats"]["library.gc_gen2"]["count"] == 1
    assert tuple(gc.callbacks) == callbacks


def test_old_gc_probe_does_not_collect_after_restart():
    monitor = UiPerfMonitor()
    monitor.enable_detailed_capture(True)
    with monitor.observe_gc("library"):
        restart(monitor)
        gc.collect(2)
    assert monitor.performance_report()["no_samples"]


def test_old_gc_drop_count_cannot_pollute_new_capture():
    monitor = UiPerfMonitor(max_samples=20)
    monitor.enable_detailed_capture(True)
    with monitor.observe_gc("library"):
        for _ in range(25):
            gc.collect(0)
        restart(monitor)
    assert monitor.performance_report()["gc_samples_dropped"] == 0


def test_new_gc_probe_inside_old_scope_still_records_exactly_once():
    monitor = UiPerfMonitor()
    monitor.enable_detailed_capture(True)
    original = tuple(gc.callbacks)
    with monitor.observe_gc("library"):
        restart(monitor)
        with monitor.observe_gc("library"):
            gc.collect(2)
        assert len(monitor.performance_report()["events"]) == 1
    report = monitor.performance_report()
    assert len(report["events"]) == 1
    assert report["stats"]["library.gc_gen2"]["count"] == 1
    assert tuple(gc.callbacks) == original


def test_epoch_is_checked_at_timeline_insertion_even_if_capture_changes_during_flush():
    monitor = UiPerfMonitor()
    monitor.enable_detailed_capture(True)
    original = monitor.record_phase
    def record(*args, **kwargs):
        restart(monitor)
        return original(*args, **kwargs)
    monitor.record_phase = record
    mark = _render_phase_probe(NS(ui_perf=monitor), clock(), "library", 10.0)
    mark("row_projection")
    assert monitor.performance_report()["events"] == []
    assert not mark.is_current()


def test_stopped_capture_keeps_its_own_drop_count_and_cleans_up_on_exception():
    monitor = UiPerfMonitor(max_samples=20)
    monitor.enable_detailed_capture(True)
    callbacks = tuple(gc.callbacks)
    with pytest.raises(RuntimeError, match="operation failed"):
        with monitor.observe_gc("library"):
            for _ in range(25):
                gc.collect(0)
            monitor.enable_detailed_capture(False)
            raise RuntimeError("operation failed")
    assert monitor.performance_report()["gc_samples_dropped"] == 5
    assert tuple(gc.callbacks) == callbacks


@pytest.mark.parametrize("during", ["projection", "model_update"])
def test_old_recent_render_cannot_publish_into_new_capture(during):
    m = rig()
    m.monitor.enable_detailed_capture(True)
    m.page.get_root = lambda: object()
    shown = []
    def show(rows):
        if during == "model_update":
            restart(m.monitor)
        gc.collect(2)
        shown.append(rows)
    m.page.show_rows = show
    window = NS(ui_perf=m.monitor, recent_page=m.page,
                _ui_perf_frame_probe=m.probe, search_controls=object(),
                _attach_row_facets=lambda *_args: None)
    rows = [{"key": "private-book"}]
    def project(items, **kwargs):
        if during == "projection":
            restart(m.monitor)
        return items, {}
    render_recent(window, rows, perf_counter=clock(),
                  project_recent_rows=project, BookListItem=object)
    assert m.monitor.performance_report()["events"] == []
    assert m.frame.callbacks == {}
    assert shown == [rows]
    assert m.monitor.snapshot()["render_recent"]["count"] == 1


@pytest.mark.parametrize("during", ["projection", "model_update"])
def test_old_render_cannot_rearm_a_frame_or_publish_phases_after_restart(during):
    rig, window, render = mounted()
    monitor = rig.monitor
    monitor.enable_detailed_capture(True)
    window.library_page.get_root = lambda: object()
    original_show = window.library_page.show_rows
    def show(*args, **kwargs):
        if during == "model_update":
            restart(monitor)
        gc.collect(2)
        return original_show(*args, **kwargs)
    window.library_page.show_rows = show
    if during == "projection":
        original = window.library_controls.state
        def state():
            restart(monitor)
            return original()
        window.library_controls.state = state
    render(clock())
    assert monitor.performance_report()["events"] == []
    assert rig.frame.callbacks == {}
    assert window.ui_perf.snapshot()["render_library"]["count"] == 1
