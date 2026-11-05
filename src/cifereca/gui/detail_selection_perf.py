"""P1 #4524: one synchronous GTK detail-selection timing context.

Never retains title, book, identity, widget or account in Diagnostics. Only
an explicit detailed capture installs a context; it is reset before return.
Inclusive selection/render phases must not be added to nested subphases.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import time


_ACTIVE = ContextVar("cifereca_detail_selection_perf", default=None)


class DetailSelectionTrace:
    def __init__(self, monitor, clock):
        self.monitor = monitor
        self.clock = clock
        self.started = clock()
        self.generation = monitor.capture_generation
        self.operation_id = monitor.new_operation_id()

    def current(self):
        return (
            self.monitor.detailed_capture_enabled is True
            and self.monitor.capture_generation == self.generation
        )

    def record(self, phase, started):
        if not self.current():
            return
        try:
            self.monitor.record_phase(
                "detail", phase, (self.clock() - started) * 1000,
                operation_id=self.operation_id, trigger="navigation",
                capture_generation=self.generation,
            )
        except Exception:
            # Metrics must never alter GTK selection, actions or provider reads.
            pass


@contextmanager
def capture_detail_selection(window, *, clock=time.perf_counter):
    monitor = getattr(window, "ui_perf", None)
    if getattr(monitor, "detailed_capture_enabled", False) is not True:
        yield None
        return
    try:
        trace = DetailSelectionTrace(monitor, clock)
    except Exception:
        yield None
        return
    token = _ACTIVE.set(trace)
    try:
        yield trace
    finally:
        _ACTIVE.reset(token)


def current_detail_trace():
    """Return only the active synchronous selection trace, if any."""
    return _ACTIVE.get()
