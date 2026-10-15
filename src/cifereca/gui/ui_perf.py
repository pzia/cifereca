from __future__ import annotations

import logging
import math
import statistics
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Callable, Iterator

_LOG = logging.getLogger("cifereca")

# Only enumerated public operation labels may enter a performance export.
_PERF_CATEGORIES = frozenset({
    "task", "opening-sync", "deep-sync", "source-sync", "sync-preflight",
    "kobo-acquisition", "maintenance", "library", "recent", "search", "plugin", "other",
})
_PERF_PHASES = frozenset({
    "queue_wait", "worker", "gtk_delivery_wait", "lock_wait", "provider",
    "storage", "projection", "row_projection", "facets", "model_update",
    "selection", "render", "frame_wait",
})
_PERF_TRIGGERS = frozenset({
    "automatic", "user", "navigation", "maintenance", "unspecified",
})
_PERF_OUTCOMES = frozenset({"success", "failed", "cancelled", "unknown"})

# Public, fixed names for historical passive UI timings. These reach Settings
# Diagnostics via snapshot() as dictionary keys, so arbitrary page titles or
# plugin-provided operation names must never be retained (P1 #1741).
_PASSIVE_UI_OPERATIONS = frozenset({
    "render", "render_library", "render_recent",
    "startup.window_presented", "startup.plugins_activation",
    "startup.shell_interactive",
    "list.append", "list.append-fast", "list.replace", "other",
})



class UiPerfMonitor:
    """Tiny rolling UI timing monitor.

    The monitor is intentionally dependency-free so it can be kept enabled in
    normal builds.  It logs slow UI work immediately and can expose p50/p95 for
    diagnostics without introducing a profiler runtime dependency.
    """

    def __init__(
        self, *, max_samples: int = 200, slow_ms: float = 50.0,
        max_events: int = 240, measurement_origin: str = "observed",
        clock: Callable[[], float] | None = None,
    ):
        self.max_samples = min(1000, max(20, int(max_samples)))
        self.slow_ms = float(slow_ms)
        self._samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.max_samples))
        self._lock = threading.Lock()
        self._phase_samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.max_samples)
        )
        self._max_events = min(1000, max(1, int(max_events)))
        self._phase_events: deque[dict[str, object]] = deque(maxlen=self._max_events)
        self._detailed_capture = False
        self._events_dropped = 0
        self._invalid_samples = 0
        self._clock = clock or time.monotonic
        self._started_at = self._clock()
        self._measurement_origin = (
            measurement_origin if measurement_origin in {"observed", "simulated"}
            else "unknown"
        )

    def enable_detailed_capture(self, enabled: bool) -> None:
        """Explicit opt-in timeline: aggregate measurements stay passive."""
        if type(enabled) is not bool:
            raise ValueError("invalid_performance_capture_option")
        with self._lock:
            if enabled and not self._detailed_capture:
                self._phase_events.clear()
                self._events_dropped = 0
                self._started_at = self._clock()
            self._detailed_capture = enabled

    @property
    def detailed_capture_enabled(self) -> bool:
        """Read-only opt-in flag for low-overhead GTK phase probes."""
        with self._lock:
            return self._detailed_capture

    def record_phase(
        self, category: str, phase: str, duration_ms: float, *,
        operation_id: int | None = None,
        trigger: str = "unspecified", outcome: str = "unknown",
    ) -> bool:
        """Store observed elapsed time only; reject raw private metadata."""
        try:
            # bool is an int subclass but never a measured duration.
            elapsed = float(duration_ms) if type(duration_ms) is not bool else float("nan")
        except (TypeError, ValueError, OverflowError):
            elapsed = float("nan")
        if not math.isfinite(elapsed) or not 0 <= elapsed <= 86_400_000:
            with self._lock:
                self._invalid_samples += 1
            return False
        category = category if type(category) is str and category in _PERF_CATEGORIES else "other"
        if type(phase) is not str or phase not in _PERF_PHASES:
            with self._lock:
                self._invalid_samples += 1
            return False
        trigger = trigger if type(trigger) is str and trigger in _PERF_TRIGGERS else "unspecified"
        outcome = outcome if type(outcome) is str and outcome in _PERF_OUTCOMES else "unknown"
        operation_id = (
            operation_id if type(operation_id) is int
            and 0 < operation_id <= 1_000_000_000 else None
        )
        key = f"{category}.{phase}"
        with self._lock:
            self._phase_samples[key].append(elapsed)
            if self._detailed_capture:
                if len(self._phase_events) == self._max_events:
                    self._events_dropped += 1
                self._phase_events.append({
                    "operation_id": operation_id,
                    "category": category,
                    "phase": phase,
                    "elapsed_ms": round(elapsed, 2),
                    "at_ms": round(max(0.0, (self._clock() - self._started_at) * 1000), 2),
                    "trigger": trigger,
                    "outcome": outcome,
                })
        return True

    def performance_report(self) -> dict[str, object]:
        """Versioned, bounded, offline export; no raw operation labels."""
        with self._lock:
            samples = {
                key: list(values) for key, values in self._phase_samples.items()
                if values
            }
            events = [dict(row) for row in self._phase_events]
            enabled = self._detailed_capture
            dropped = self._events_dropped
            invalid = self._invalid_samples
        stats = {}
        for key, values in sorted(samples.items()):
            ordered = sorted(values)
            p95 = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95))))
            stats[key] = {
                "count": len(ordered),
                "p50_ms": round(statistics.median(ordered), 2),
                "p95_ms": round(ordered[p95], 2),
                "max_ms": round(ordered[-1], 2),
            }
        return {
            "schema": "cifereca.performance.phases/1",
            "measurement_origin": self._measurement_origin,
            "no_samples": not bool(stats),
            "detailed_capture_enabled": enabled,
            "stats": stats,
            "events": events,
            "events_dropped": dropped,
            "invalid_samples": invalid,
            "limits": {
                "max_samples_per_phase": self.max_samples,
                "max_events": self._max_events,
            },
            "remote_upload": False,
        }

    def record(self, operation: str, duration_ms: float) -> None:
        """Record legacy passive timings without keeping untrusted labels.

        The bounded names used by snapshot() are also safe to put in TRACE.
        Unknown operation strings collapse to one bucket rather than creating
        an unbounded cardinality of retained series.
        """
        try:
            value = float(duration_ms) if type(duration_ms) is not bool else float("nan")
        except (TypeError, ValueError, OverflowError):
            value = float("nan")
        if not math.isfinite(value) or not 0 <= value <= 86_400_000:
            with self._lock:
                self._invalid_samples += 1
            return
        name = (
            operation if type(operation) is str
            and operation in _PASSIVE_UI_OPERATIONS else "other"
        )
        with self._lock:
            self._samples[name].append(value)
        if value >= self.slow_ms:
            _LOG.info("TRACE ui-perf slow operation=%s duration_ms=%.1f", name, value)

    @contextmanager
    def measure(self, operation: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(operation, (time.perf_counter() - started) * 1000.0)

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        out: dict[str, dict[str, float | int]] = {}
        with self._lock:
            items = {key: list(values) for key, values in self._samples.items()}
        for key, values in items.items():
            if not values:
                continue
            ordered = sorted(values)
            p50 = statistics.median(ordered)
            p95_index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95))))
            out[key] = {
                "count": len(ordered),
                "p50_ms": round(p50, 2),
                "p95_ms": round(ordered[p95_index], 2),
                "max_ms": round(max(ordered), 2),
            }
        return out
