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
    "kobo-acquisition", "maintenance", "library", "search", "plugin", "other",
})
_PERF_PHASES = frozenset({
    "queue_wait", "worker", "gtk_delivery_wait", "lock_wait", "provider",
    "storage", "projection", "render", "frame_wait",
})
_PERF_TRIGGERS = frozenset({
    "automatic", "user", "navigation", "maintenance", "unspecified",
})
_PERF_OUTCOMES = frozenset({"success", "failed", "cancelled", "unknown"})



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

    def record(self, operation: str, duration_ms: float) -> None:
        value = float(duration_ms)
        with self._lock:
            self._samples[str(operation)].append(value)
        if value >= self.slow_ms:
            _LOG.info("TRACE ui-perf slow operation=%s duration_ms=%.1f", operation, value)

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
