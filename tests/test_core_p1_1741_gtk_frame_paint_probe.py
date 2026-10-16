"""#1741: first GDK after-paint signal is an opt-in bounded observation.

The sample is measured AFTER the synchronous render; it is not a display
presentation timestamp, GPU latency or proof of a visible pixel.
"""
from types import SimpleNamespace as NS

from cifereca.gui.gtk_frame_paint_probe import GtkFramePaintProbe
from cifereca.gui.library_render_controller import _observe_after_paint
from cifereca.gui.ui_perf import UiPerfMonitor
from cifereca.gui.window_close import close_window_resources


class FrameClock:
    def __init__(self):
        self.callbacks = {}
        self.last_id = 0

    def connect(self, signal, callback):
        assert signal == "after-paint"
        self.last_id += 1
        self.callbacks[self.last_id] = callback
        return self.last_id

    def disconnect(self, handler):
        self.callbacks.pop(handler, None)

    def emit(self):
        for cb in tuple(self.callbacks.values()):
            cb(self)


class Timers:
    def __init__(self):
        self.next = 0
        self.callbacks = {}
        self.removed = []

    def add(self, ms, callback):
        assert ms == 1000
        self.next += 1
        self.callbacks[self.next] = callback
        return self.next

    def remove(self, timer):
        self.removed.append(timer)
        self.callbacks.pop(timer, None)

    def expire(self):
        callbacks = tuple(self.callbacks.values())
        self.callbacks.clear()
        for callback in callbacks:
            assert callback() is False


def rig(*, mapped=True):
    seconds = [10.0]
    now = lambda: seconds[0]
    monitor = UiPerfMonitor(
        clock=now, measurement_origin="simulated",
        resource_reader=lambda: {"cpu_ms": 1.0, "rss_bytes": 0},
    )
    timers = Timers()
    clock = FrameClock()
    page = NS(
        get_mapped=lambda: mapped,
        get_frame_clock=lambda: clock,
    )
    probe = GtkFramePaintProbe(
        timeout_add=timers.add, source_remove=timers.remove, clock=now,
    )
    return NS(seconds=seconds, now=now, monitor=monitor, timers=timers,
              frame=clock, page=page, probe=probe)


def test_no_probe_without_explicit_capture_or_mapped_widget():
    m = rig()
    assert not m.probe.observe("library", m.page, m.monitor)
    assert m.frame.callbacks == {} and m.timers.callbacks == {}
    m.monitor.enable_detailed_capture(True)
    m = rig(mapped=False)
    m.monitor.enable_detailed_capture(True)
    assert not m.probe.observe("recent", m.page, m.monitor)
    assert m.frame.callbacks == {} and m.timers.callbacks == {}
    assert not m.probe.observe("unsafe/private", m.page, m.monitor)
    assert m.monitor.performance_report()["no_samples"]


def test_first_actual_after_paint_signal_produces_one_bounded_sample():
    m = rig()
    m.monitor.enable_detailed_capture(True)
    assert m.probe.observe("library", m.page, m.monitor)
    assert len(m.frame.callbacks) == 1
    assert len(m.timers.callbacks) == 1
    m.seconds[0] += .016
    m.frame.emit()
    m.frame.emit()  # no repeated sample
    report = m.monitor.performance_report()
    assert report["stats"]["library.frame_wait"]["count"] == 1
    assert report["stats"]["library.frame_wait"]["p50_ms"] == 16
    assert report["events"][0]["phase"] == "frame_wait"
    assert report["events"][0]["category"] == "library"
    assert report["measurement_origin"] == "simulated"
    assert m.frame.callbacks == {} and m.timers.callbacks == {}


def test_superseded_or_timed_out_render_does_not_get_false_frame():
    m = rig()
    m.monitor.enable_detailed_capture(True)
    assert m.probe.observe("recent", m.page, m.monitor)
    m.seconds[0] += .007
    assert m.probe.observe("recent", m.page, m.monitor)
    assert len(m.frame.callbacks) == 1
    m.seconds[0] += .023
    m.frame.emit()
    assert m.monitor.performance_report()["stats"]["recent.frame_wait"]["p50_ms"] == 23
    assert m.probe.observe("recent", m.page, m.monitor)
    m.timers.expire()
    m.seconds[0] += .010
    m.frame.emit()
    assert m.monitor.performance_report()["stats"]["recent.frame_wait"]["count"] == 1


def test_stopping_capture_or_disposing_probe_ignores_late_frame():
    m = rig()
    m.monitor.enable_detailed_capture(True)
    m.probe.observe("library", m.page, m.monitor)
    m.monitor.enable_detailed_capture(False)
    m.seconds[0] += .010
    m.frame.emit()
    assert m.monitor.performance_report()["no_samples"]
    m.monitor.enable_detailed_capture(True)
    m.probe.observe("library", m.page, m.monitor)
    m.probe.dispose()
    m.frame.emit()
    assert m.monitor.performance_report()["no_samples"]
    assert m.frame.callbacks == {} and m.timers.callbacks == {}
    assert not m.probe.observe("library", m.page, m.monitor)


def test_unrelated_late_frame_over_one_second_is_absent_not_zero():
    m = rig()
    m.monitor.enable_detailed_capture(True)
    m.probe.observe("library", m.page, m.monitor)
    m.seconds[0] += 1.5
    m.frame.emit()
    assert m.monitor.performance_report()["no_samples"]


def test_library_controller_forwards_only_on_opt_in_and_close_disposes():
    m = rig()
    host = NS(_closed=False, ui_perf=m.monitor, _ui_perf_frame_probe=m.probe)
    _observe_after_paint(host, "library", m.page, m.now)
    assert m.frame.callbacks == {}
    m.monitor.enable_detailed_capture(True)
    _observe_after_paint(host, "library", m.page, m.now)
    assert len(m.frame.callbacks) == 1
    close_window_resources(host, source_remove=lambda _timer: None)
    assert host._closed
    assert m.frame.callbacks == {} and m.timers.callbacks == {}
    assert getattr(host, "_ui_perf_frame_probe", None) is None
    m.seconds[0] += .010
    m.frame.emit()
    assert m.monitor.performance_report()["no_samples"]
