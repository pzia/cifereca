"""P1 #4524: bounded, correlated baseline for GTK book-detail selection.

All samples in these unit tests are synthetic; no GDK, provider or GTK
session is claimed. Actual after-paint measures a frame-clock boundary only.
"""
from pathlib import Path
from types import SimpleNamespace as NS
import ast

from cifereca.gui.detail_selection_perf import (
    capture_detail_selection, current_detail_trace,
)
from cifereca.gui.ui_perf import UiPerfMonitor
from tests.test_core_p1_1741_gtk_frame_paint_probe import rig


def _monitor():
    return UiPerfMonitor(
        measurement_origin="simulated",
        resource_reader=lambda: {"cpu_ms": 0.0, "rss_bytes": 0},
    )


def test_no_trace_or_frame_without_explicit_opt_in():
    monitor = _monitor()
    with capture_detail_selection(NS(ui_perf=monitor)) as trace:
        assert trace is None
        assert current_detail_trace() is None
    m = rig()
    assert m.probe.observe("detail", m.page, monitor) is False
    assert m.frame.callbacks == {}
    assert monitor.performance_report()["no_samples"]


def test_synchronous_phases_share_ephemeral_operation_id_and_clear_scope():
    monitor = _monitor()
    monitor.enable_detailed_capture(True)
    seconds = [10.0]
    clock = lambda: seconds[0]
    with capture_detail_selection(NS(ui_perf=monitor), clock=clock) as trace:
        assert trace is current_detail_trace()
        seconds[0] += 0.011
        trace.record("projection", trace.started)
        sections_started = clock()
        seconds[0] += 0.005
        trace.record("sections", sections_started)
        seconds[0] += 0.004
        trace.record("render", trace.started)
        trace.record("selection", trace.started)
    assert current_detail_trace() is None
    events = monitor.performance_report()["events"]
    assert [e["phase"] for e in events] == [
        "projection", "sections", "render", "selection",
    ]
    assert {e["category"] for e in events} == {"detail"}
    assert {e["operation_id"] for e in events} == {trace.operation_id}
    assert all(e["trigger"] == "navigation" for e in events)
    assert monitor.performance_report()["measurement_origin"] == "simulated"


def test_stop_restart_disallows_old_trace_and_exception_clears_context():
    monitor = _monitor()
    monitor.enable_detailed_capture(True)
    try:
        with capture_detail_selection(NS(ui_perf=monitor)) as trace:
            monitor.enable_detailed_capture(False)
            monitor.enable_detailed_capture(True)
            trace.record("selection", trace.started)
            raise RuntimeError("synthetic widget failure")
    except RuntimeError:
        pass
    assert current_detail_trace() is None
    assert monitor.performance_report()["events"] == []


def test_first_gdk_after_paint_is_correlated_but_not_pixel_latency():
    m = rig()
    m.monitor.enable_detailed_capture(True)
    with capture_detail_selection(NS(ui_perf=m.monitor), clock=m.now) as trace:
        m.seconds[0] += .006
        trace.record("selection", trace.started)
        assert m.probe.observe(
            "detail", m.page, m.monitor, clock=m.now,
            operation_id=trace.operation_id, selection_started=trace.started,
        )
    m.seconds[0] += .018
    m.frame.emit()
    m.frame.emit()
    events = m.monitor.performance_report()["events"]
    assert [e["phase"] for e in events] == ["selection", "frame_wait", "first_paint"]
    assert {e["operation_id"] for e in events} == {trace.operation_id}
    assert events[-1]["elapsed_ms"] == 24
    assert m.frame.callbacks == {} and m.timers.callbacks == {}


def test_reselection_empty_or_unmapped_drops_pending_first_paint():
    m = rig()
    m.monitor.enable_detailed_capture(True)
    assert m.probe.observe(
        "detail", m.page, m.monitor, clock=m.now, selection_started=m.now(),
    )
    m.probe.cancel("detail")
    m.frame.emit()
    assert m.monitor.performance_report()["no_samples"]
    assert m.frame.callbacks == {}
    m.probe.dispose()
    assert not m.probe.observe("detail", m.page, m.monitor)
    hidden = rig(mapped=False)
    hidden.monitor.enable_detailed_capture(True)
    assert not hidden.probe.observe("detail", hidden.page, hidden.monitor)
    assert hidden.monitor.performance_report()["no_samples"]


def test_capture_stop_or_timeout_cannot_attribute_late_frame_to_new_book():
    m = rig()
    m.monitor.enable_detailed_capture(True)
    assert m.probe.observe("detail", m.page, m.monitor, clock=m.now,
                           selection_started=m.now())
    m.monitor.enable_detailed_capture(False)
    m.monitor.enable_detailed_capture(True)
    m.seconds[0] += .008
    m.frame.emit()
    assert m.monitor.performance_report()["no_samples"]
    assert m.probe.observe("detail", m.page, m.monitor, clock=m.now,
                           selection_started=m.now())
    m.seconds[0] += 1.3
    m.frame.emit()
    assert m.monitor.performance_report()["no_samples"]


def test_integration_hooks_exist_without_modifying_detail_layout():
    gui = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
    files = ("master_detail.py", "detail_panel.py", "detail_facets_controller.py",
             "library_detail_controller.py", "gtk_frame_paint_probe.py")
    for name in files:
        ast.parse((gui / name).read_text(encoding="utf-8"))
    master = (gui / files[0]).read_text(encoding="utf-8")
    assert "capture_detail_selection(self.window" in master
    assert "selection_started=trace.started" in master
    panel = (gui / files[1]).read_text(encoding="utf-8")
    assert 'trace.record("sections", sections_started)' in panel
    assert 'trace.record("actions", started)' in panel
    assert 'trace.record("render", started)' in panel
    assert 'trace.record("facets", started)' in (gui / files[2]).read_text(encoding="utf-8")
    assert 'trace.record("projection", projection_started)' in (gui / files[3]).read_text(encoding="utf-8")
