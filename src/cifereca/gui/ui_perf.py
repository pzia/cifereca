from __future__ import annotations

import logging
import os
import sys
import math
import statistics
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Callable, Iterator

_LOG = logging.getLogger("cifereca")

def _p95_nearest_rank_index(size: int) -> int:
    """Nearest-rank p95 index, consistent for exports and passive snapshots.

    With 14 observations p95 is the 14th (maximum), not the 13th.
    Small sample counts are always shown alongside the percentile.
    """
    return max(0, math.ceil(size * 0.95) - 1)


def _process_resource_sample() -> dict[str, float | int | None]:
    """One explicit, local sample: process CPU and Linux resident pages.

    /proc/self/statm exposes *current* RSS, not a high-water mark. Unsupported
    platforms or unavailable procfs return None, never a fabricated zero.
    This function is only called on capture state transitions, not per render.
    """
    cpu_ms = time.process_time() * 1000.0
    rss_bytes = None
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/statm", encoding="ascii") as handle:
                fields = handle.read(128).split()
            resident_pages = int(fields[1])
            page_bytes = os.sysconf("SC_PAGE_SIZE")
            if 0 <= resident_pages and 0 < page_bytes:
                rss_bytes = resident_pages * page_bytes
        except (OSError, ValueError, IndexError, OverflowError):
            pass
    return {"cpu_ms": cpu_ms, "rss_bytes": rss_bytes}


# Only enumerated public operation labels may enter a performance export.
_PERF_CATEGORIES = frozenset({
    "task", "opening-sync", "deep-sync", "source-sync", "source-materialize",
    "sync-preflight", "kobo-acquisition", "maintenance", "library", "recent",
    "search", "plugin", "other",
    # Fixed Core UI/MainWindow task kinds already present at the submit call.
    # Without this exact allowlist, an opt-in 600-file Maintenance audit is
    # reported as "other.worker" despite being a known maintenance action.
    # Keep untrusted/plugin-defined task labels out of performance exports.
    "maintenance-plan", "maintenance-tool", "maintenance-inspection",
    "history", "bookings", "search-known", "detail-cache",
    "adept-inventory", "adept-return", "loan-acquisition", "borrow",
    "obtain", "provider-connect", "sync-audit", "source-consent", "identity",
})
_PERF_PHASES = frozenset({
    "queue_wait", "worker", "gtk_delivery_wait", "lock_wait", "provider",
    "storage", "projection", "row_projection", "facets", "model_update",
    "selection", "render", "frame_wait", "gc_gen0", "gc_gen1", "gc_gen2",
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
        resource_reader: Callable[[], dict[str, object]] | None = None,
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
        self._capture_generation = 0
        self._operation_sequence = 0
        self._events_dropped = 0
        self._invalid_samples = 0
        self._gc_samples_dropped = 0
        self._active_gc_probes: set[tuple[int, str, int]] = set()
        self._clock = clock or time.monotonic
        self._started_at = self._clock()
        self._resource_reader = resource_reader or _process_resource_sample
        self._resource_start: dict[str, float | int | None] | None = None
        self._resource_end: dict[str, float | int | None] | None = None
        self._resource_stopped_at: float | None = None
        self._measurement_origin = (
            measurement_origin if type(measurement_origin) is str
            and measurement_origin in {"observed", "simulated"}
            else "unknown"
        )

    def _sample_resources(self) -> dict[str, float | int | None] | None:
        """Validate at ingress; never retain raw reader payloads or errors."""
        try:
            raw = self._resource_reader()
            if not isinstance(raw, dict):
                return None
            cpu = raw.get("cpu_ms")
            if type(cpu) not in (float, int) or not math.isfinite(cpu):
                return None
            if not 0 <= cpu <= 1_000_000_000_000:
                return None
            resident = raw.get("rss_bytes")
            if type(resident) is not int or not 0 <= resident <= 1_000_000_000_000_000:
                resident = None
            return {"cpu_ms": round(cpu, 2), "rss_bytes": resident}
        except Exception:
            # Never expose procfs/reader exception strings to Diagnostics.
            return None

    def enable_detailed_capture(self, enabled: bool) -> None:
        """Explicit opt-in timeline: aggregate measurements stay passive."""
        if type(enabled) is not bool:
            raise ValueError("invalid_performance_capture_option")
        with self._lock:
            if enabled and not self._detailed_capture:
                self._capture_generation += 1
                self._phase_events.clear()
                self._events_dropped = 0
                self._gc_samples_dropped = 0
                self._started_at = self._clock()
                self._resource_start = self._sample_resources()
                self._resource_end = None
                self._resource_stopped_at = None
            elif not enabled and self._detailed_capture:
                self._capture_generation += 1
                self._resource_end = self._sample_resources()
                self._resource_stopped_at = self._clock()
            self._detailed_capture = enabled

    @property
    def detailed_capture_enabled(self) -> bool:
        """Read-only opt-in flag for low-overhead GTK phase probes."""
        with self._lock:
            return self._detailed_capture

    @property
    def capture_generation(self) -> int:
        """Epoch for invalidating GTK callbacks across stop/start transitions."""
        with self._lock:
            return self._capture_generation

    def new_operation_id(self) -> int | None:
        """Ephemeral correlation key; no identity, title, source or account."""
        with self._lock:
            if not self._detailed_capture:
                return None
            self._operation_sequence = (self._operation_sequence % 1_000_000_000) + 1
            return self._operation_sequence

    def record_phase(
        self, category: str, phase: str, duration_ms: float, *,
        operation_id: int | None = None,
        trigger: str = "unspecified", outcome: str = "unknown",
        observed_at_ms: float | None = None,
        capture_generation: int | None = None,
    ) -> bool:
        """Store elapsed time; optional epoch limits detailed timeline delivery.

        Passive statistics remain process-wide. Buffered samples from an old
        opt-in probe must not enter a new capture's detailed timeline.
        """
        # Plugin and GTK callback values are not conversion instructions.
        # Only exact built-in numeric types are trusted; never invoke a
        # provider object's __float__, which could raise or do arbitrary I/O.
        try:
            elapsed = (
                float(duration_ms) if type(duration_ms) in (int, float)
                else float("nan")
            )
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
        # An opt-in probe may deliver a bounded sample after GTK finishes;
        # preserve when it was observed rather than misdating it at flush.
        if observed_at_ms is not None:
            try:
                observed_at_ms = (
                    float(observed_at_ms)
                    if type(observed_at_ms) in (int, float) else None
                )
            except (TypeError, ValueError, OverflowError):
                observed_at_ms = None
            if observed_at_ms is not None and (
                not math.isfinite(observed_at_ms)
                or not 0 <= observed_at_ms <= 86_400_000
            ):
                observed_at_ms = None
        # Never invoke a user-defined __eq__ inside the monitor lock.
        # Malformed capture epochs may still feed passive statistics, but
        # must never leak into a new opt-in chronology.
        if capture_generation is not None and type(capture_generation) is not int:
            capture_generation = -1
        key = f"{category}.{phase}"
        with self._lock:
            self._phase_samples[key].append(elapsed)
            if self._detailed_capture and (
                capture_generation is None
                or capture_generation == self._capture_generation
            ):
                if len(self._phase_events) == self._max_events:
                    self._events_dropped += 1
                self._phase_events.append({
                    "operation_id": operation_id,
                    "category": category,
                    "phase": phase,
                    "elapsed_ms": round(elapsed, 2),
                    "at_ms": round(
                        observed_at_ms if observed_at_ms is not None
                        else max(0.0, (self._clock() - self._started_at) * 1000),
                        2,
                    ),
                    "trigger": trigger,
                    "outcome": outcome,
                })
        return True

    @contextmanager
    def observe_gc(self, category: str) -> Iterator[None]:
        """Measure Python GC pauses during *one* explicit opt-in UI operation.

        The hook is local to a GTK model update, not a process-wide profiler:
        it registers only while detailed capture is active, ignores collections
        on other threads and removes itself on success or failure. Samples are
        bounded before reaching the monitor and carry no GC object metadata.
        The phase names identify generation, not a causal performance verdict.
        """
        if not self.detailed_capture_enabled:
            yield
            return
        import gc

        category = (
            category if type(category) is str and category in _PERF_CATEGORIES
            else "other"
        )
        owner = threading.get_ident()
        with self._lock:
            capture_generation = self._capture_generation
            key = (owner, category, capture_generation)
            if key in self._active_gc_probes:
                # A re-entrant GTK update is already covered by the outer
                # interval; recording both would double count its GC pause.
                nested = True
            else:
                self._active_gc_probes.add(key)
                nested = False
        if nested:
            yield
            return

        started: dict[int, float] = {}
        samples: list[tuple[int, float, float]] = []
        dropped = 0
        capacity = self.max_samples

        def callback(stage: str, info: dict[str, object]) -> None:
            nonlocal dropped
            # Keep the global GC callback allocation-free on unrelated
            # threads and avoid acquiring the monitor lock inside GC.
            if (threading.get_ident() != owner or not self._detailed_capture
                    or self._capture_generation != capture_generation):
                return
            generation = info.get("generation") if isinstance(info, dict) else None
            if type(generation) is not int or generation not in (0, 1, 2):
                return
            if stage == "start":
                started[generation] = time.perf_counter()
            elif stage == "stop":
                initial = started.pop(generation, None)
                if initial is None:
                    return
                elapsed_ms = (time.perf_counter() - initial) * 1000
                observed_at_ms = (self._clock() - self._started_at) * 1000
                if len(samples) < capacity:
                    samples.append((generation, elapsed_ms, observed_at_ms))
                else:
                    dropped += 1

        installed = False
        try:
            try:
                gc.callbacks.append(callback)
                installed = True
            except Exception:
                # Instrumentation must not block a real GTK operation.
                pass
            yield
        finally:
            if installed:
                try:
                    gc.callbacks.remove(callback)
                except ValueError:
                    pass
            with self._lock:
                self._active_gc_probes.discard(key)
                # A stop alone still describes this capture, but a restart
                # resets its counters. An older probe cannot refill them.
                if (self._capture_generation == capture_generation
                        or (not self._detailed_capture
                            and self._capture_generation == capture_generation + 1)):
                    self._gc_samples_dropped += dropped
            # No record_phase/lock/JSON allocation inside a GC callback.
            # Flush after the GTK operation to avoid callback re-entrancy.
            for generation, elapsed_ms, observed_at_ms in samples:
                self.record_phase(
                    category, f"gc_gen{generation}", elapsed_ms,
                    trigger="navigation", observed_at_ms=observed_at_ms,
                    capture_generation=capture_generation,
                )

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
            gc_dropped = self._gc_samples_dropped
            resource_start = dict(self._resource_start) if self._resource_start else None
            resource_end = dict(self._resource_end) if self._resource_end else None
            resource_stopped_at = self._resource_stopped_at
            resource_started_at = self._started_at
        # CPU is process-wide (all threads), not a task or GTK phase metric.
        # A stopped interval has two samples; active captures show only their
        # baseline, without a misleading partial utilization estimate.
        wall_ms = None
        cpu_ms = None
        cpu_percent = None
        rss_start = resource_start.get("rss_bytes") if resource_start else None
        rss_end = resource_end.get("rss_bytes") if resource_end else None
        if resource_end is not None and resource_start is not None and resource_stopped_at is not None:
            wall_ms = round(max(0.0, (resource_stopped_at - resource_started_at) * 1000), 2)
            cpu_ms = round(max(0.0, resource_end["cpu_ms"] - resource_start["cpu_ms"]), 2)
            if wall_ms > 0:
                # Can legitimately exceed 100% when several threads use CPU.
                cpu_percent = round(cpu_ms * 100 / wall_ms, 2)
        resources = {
            "state": "active" if enabled else (
                "stopped" if resource_stopped_at is not None else "not_started"
            ),
            "sample_count": int(resource_start is not None) + int(resource_end is not None),
            "wall_ms": wall_ms,
            "process_cpu_ms": cpu_ms,
            "process_cpu_percent_one_core": cpu_percent,
            "rss_start_bytes": rss_start,
            "rss_end_bytes": rss_end,
            "rss_source": "linux_proc_statm_current" if (
                rss_start is not None or rss_end is not None
            ) else "unavailable",
            "peak_rss_measured": False,
        }
        stats = {}
        for key, values in sorted(samples.items()):
            ordered = sorted(values)
            p95 = _p95_nearest_rank_index(len(ordered))
            stats[key] = {
                "count": len(ordered),
                "p50_ms": round(statistics.median(ordered), 2),
                "p95_ms": round(ordered[p95], 2),
                "max_ms": round(ordered[-1], 2),
            }
        return {
            "schema": "cifereca.performance.phases/1",
            "percentile_method": "nearest_rank",
            "measurement_origin": self._measurement_origin,
            "no_samples": not bool(stats),
            "detailed_capture_enabled": enabled,
            "stats": stats,
            "events": events,
            "events_dropped": dropped,
            "invalid_samples": invalid,
            "gc_samples_dropped": gc_dropped,
            "resource_window": resources,
            # Separate from the stable stats/resource_window v1 contracts:
            # callers asserting exact snapshots remain backward compatible.
            "evidence": {
                "phases": {
                    key: {
                        "sample_coverage": (
                            "limited" if len(values) < 20 else "bounded_rolling"
                        ),
                        "p95_interpretation": "sample_nearest_rank_only",
                    }
                    for key, values in sorted(samples.items())
                },
                "resources": {
                    "sampling_strategy": (
                        "start_end_only" if resource_start is not None and resource_end is not None
                        else "start_only" if resource_start is not None
                        else "end_only" if resource_end is not None else "unavailable"
                    ),
                    "peak_rss_bytes": None,
                },
            },
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
            value = (
                float(duration_ms) if type(duration_ms) in (int, float)
                else float("nan")
            )
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
            p95_index = _p95_nearest_rank_index(len(ordered))
            out[key] = {
                "count": len(ordered),
                "p50_ms": round(p50, 2),
                "p95_ms": round(ordered[p95_index], 2),
                "max_ms": round(max(ordered), 2),
            }
        return out
