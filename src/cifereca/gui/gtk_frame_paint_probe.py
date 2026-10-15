"""Optional one-shot GDK after-paint observation for Library/Recent (#1741).

Not a presentation timestamp: this records the interval from the *end of the
synchronous model update* to the next after-paint signal on the widget's frame
clock. A missing/hidden/closed widget yields no sample, never zero latency.
No frame is requested, so the instrument does not force a redraw.
"""
from __future__ import annotations

import math
import time
from typing import Callable


class GtkFramePaintProbe:
    """At most one pending frame observation per allowed page."""

    _CATEGORIES = frozenset(("library", "recent"))

    def __init__(
        self, *, timeout_add: Callable, source_remove: Callable,
        clock: Callable[[], float] = time.perf_counter,
        timeout_ms: int = 1000,
    ):
        self._timeout_add = timeout_add
        self._source_remove = source_remove
        self._clock = clock
        self._timeout_ms = max(100, min(2000, int(timeout_ms)))
        self._pending: dict[str, dict] = {}
        self._closed = False

    def _remove(self, category: str, *, remove_timeout: bool = True) -> None:
        pending = self._pending.pop(category, None)
        if pending is None:
            return
        try:
            pending["frame_clock"].disconnect(pending["handler"])
        except Exception:
            pass
        timer = pending.get("timer")
        if remove_timeout and timer is not None:
            try:
                self._source_remove(timer)
            except Exception:
                pass

    def observe(
        self, category: str, widget, monitor, *, clock=None,
        operation_id: int | None = None,
    ) -> bool:
        """Observe a future frame only when the user has opted into capture."""
        if category not in self._CATEGORIES:
            return False
        self._remove(category)  # supersede any undelivered older render
        if self._closed or getattr(monitor, "detailed_capture_enabled", False) is not True:
            return False
        try:
            if not widget.get_mapped():
                return False
            frame_clock = widget.get_frame_clock()
            if frame_clock is None:
                return False
            generation = getattr(monitor, "capture_generation", None)
            now = clock or self._clock
            started = now()
            if not isinstance(started, (int, float)) or not math.isfinite(started):
                return False
        except Exception:
            return False

        token = object()

        def finished(_clock):
            current = self._pending.get(category)
            if current is None or current["token"] is not token:
                return
            self._remove(category)
            if (
                self._closed
                or getattr(monitor, "detailed_capture_enabled", False) is not True
                or getattr(monitor, "capture_generation", None) != generation
            ):
                return
            try:
                elapsed_ms = (now() - started) * 1000
                # A late, unrelated paint cannot be labelled as a response
                # to this update. Missing observations remain missing.
                if math.isfinite(elapsed_ms) and 0 <= elapsed_ms <= self._timeout_ms:
                    monitor.record_phase(
                        category, "frame_wait", elapsed_ms, trigger="navigation",
                        operation_id=operation_id,
                    )
            except Exception:
                # Diagnostics must never break the GDK frame clock.
                pass

        def expired():
            current = self._pending.get(category)
            if current is not None and current["token"] is token:
                self._remove(category, remove_timeout=False)
            return False

        try:
            handler = frame_clock.connect("after-paint", finished)
            self._pending[category] = {
                "token": token, "frame_clock": frame_clock,
                "handler": handler, "timer": None,
            }
            timer = self._timeout_add(self._timeout_ms, expired)
            current = self._pending.get(category)
            if current is not None and current["token"] is token:
                current["timer"] = timer
            return True
        except Exception:
            self._remove(category)
            return False

    def dispose(self) -> None:
        self._closed = True
        for category in tuple(self._pending):
            self._remove(category)
