"""P1 #1741: correlate one GTK render's phases and next after-paint, not private IDs."""
from itertools import count

from tests.test_core_p1_1741_gtk_frame_paint_probe import rig
from tests.test_core_p1_1741_library_render_phases import _library_context
from cifereca.gui.ui_perf import UiPerfMonitor


def clock(start=10.0):
    ticks = count()
    return lambda: start + next(ticks) * .002


def mounted():
    m = rig()
    window, entries, events, call = _library_context(m.monitor)
    window.library_page.get_mapped = lambda: True
    window.library_page.get_frame_clock = lambda: m.frame
    # The real controller reuses this probe and never imports Gtk from a
    # headless test. It must make no redraw request during observation.
    window._ui_perf_frame_probe = m.probe
    return m, window, call


def test_render_and_frame_share_one_ephemeral_operation_id():
    m, window, call = mounted()
    m.monitor.enable_detailed_capture(True)
    call(clock())
    before = m.monitor.performance_report()["events"]
    assert [e["phase"] for e in before] == [
        "row_projection", "facets", "model_update", "selection",
    ]
    operation = before[0]["operation_id"]
    assert type(operation) is int and 0 < operation <= 1_000_000_000
    assert {e["operation_id"] for e in before} == {operation}
    assert len(m.frame.callbacks) == 1
    m.frame.emit()
    after = m.monitor.performance_report()["events"]
    assert [e["phase"] for e in after] == [
        "row_projection", "facets", "model_update", "selection", "frame_wait",
    ]
    assert {e["operation_id"] for e in after} == {operation}
    assert all(e["category"] == "library" for e in after)
    assert all("token" not in repr(e) for e in after)


def test_superseded_render_gets_no_stale_frame():
    m, window, call = mounted()
    m.monitor.enable_detailed_capture(True)
    timer = clock()
    call(timer)
    first_id = m.monitor.performance_report()["events"][-1]["operation_id"]
    call(timer)
    newest_id = m.monitor.performance_report()["events"][-1]["operation_id"]
    assert first_id != newest_id
    m.frame.emit()
    events = m.monitor.performance_report()["events"]
    assert len([e for e in events if e["phase"] == "frame_wait"]) == 1
    assert events[-1]["operation_id"] == newest_id


def test_stop_and_restart_capture_reject_old_pending_gtk_frame():
    m, window, call = mounted()
    mon = m.monitor
    mon.enable_detailed_capture(True)
    first_epoch = mon.capture_generation
    call(clock())
    assert len(m.frame.callbacks) == 1
    mon.enable_detailed_capture(False)
    mon.enable_detailed_capture(True)
    assert mon.capture_generation == first_epoch + 2
    assert mon.performance_report()["events"] == []
    m.frame.emit()
    assert mon.performance_report()["events"] == []
    assert "library.frame_wait" not in mon.performance_report()["stats"]
    assert m.frame.callbacks == {}


def test_observation_generations_only_change_on_actual_transitions():
    mon = UiPerfMonitor(resource_reader=lambda: {"cpu_ms": 0.0, "rss_bytes": 0})
    assert mon.new_operation_id() is None
    assert mon.capture_generation == 0
    mon.enable_detailed_capture(False)
    assert mon.capture_generation == 0
    mon.enable_detailed_capture(True)
    assert mon.capture_generation == 1
    first = mon.new_operation_id()
    second = mon.new_operation_id()
    assert second == first + 1
    mon.enable_detailed_capture(True)
    assert mon.capture_generation == 1
    mon.enable_detailed_capture(False)
    assert mon.capture_generation == 2
    assert mon.new_operation_id() is None
    mon.enable_detailed_capture(True)
    assert mon.capture_generation == 3
    assert mon.new_operation_id() == second + 1
