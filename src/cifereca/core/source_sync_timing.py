"""Worker-local, closed numeric source-sync measurements (#2433/#1741).

Intervals may be nested: locked_work includes provider and storage work.
They are elapsed wall times, not pure network/SQLite CPU time or lock waits
(the acquisition lock is non-blocking). No account or task payload is kept.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic_ns


TIMING_FIELDS = frozenset({
    "lock_acquire_ms", "locked_work_ms", "loan_refresh_ms", "library_read_ms",
    "rights_mirror_ms", "catalog_mirror_ms", "catalog_scan_ms",
})
_CURRENT: ContextVar[dict | None] = ContextVar("source_sync_timings", default=None)


@contextmanager
def timing_scope(samples):
    token = _CURRENT.set(samples)
    try:
        yield
    finally:
        _CURRENT.reset(token)


@contextmanager
def measure(field):
    samples = _CURRENT.get()
    if samples is None or field not in TIMING_FIELDS:
        yield
        return
    try:
        started = monotonic_ns()
    except Exception:
        yield
        return
    try:
        yield
    finally:
        try:
            elapsed = (monotonic_ns() - started) // 1_000_000
            if 0 <= elapsed <= 86_400_000:
                samples[field] = elapsed
        except Exception:
            pass  # optional telemetry must preserve the provider outcome
