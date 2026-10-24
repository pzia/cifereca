from __future__ import annotations

import itertools
import queue
import threading
from collections import deque
from time import monotonic_ns
from concurrent.futures import Future
from enum import IntEnum
from typing import Any, Callable

from .diagnostic_task_context import diagnostic_task_scope
from .task_admission import SharedTaskAdmission, AdmissionLease


def _bounded_percentile_ms(samples: tuple[int, ...], percentile: int) -> float:
    """Nearest-rank p50/p95 of monotonic nanoseconds, no task data."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(0, (len(ordered) * percentile + 99) // 100 - 1)
    return round(ordered[index] / 1_000_000, 3)


class TaskPriority(IntEnum):
    """Lower values run first; gaps are intentional for future classes."""

    USER_ACTION = 0
    DEVICE_SYNC = 10
    ACTIVE_LOAN = 20
    PROVIDER_SYNC = 30
    NORMAL = 40
    METADATA = 50
    MAINTENANCE = 60


class PriorityExecutor:
    """Priority executor independent from GTK, with optional pending admission cap.

    The default preserves legacy unbounded admission; ``max_pending`` applies
    only to queued (not running) work, never adds threads, and never waits for
    space on the submitting thread. These are *local* pool metrics, not a
    global I/O/CPU quota (#2431).
    """

    def __init__(
        self, max_workers: int = 3, thread_name_prefix: str = "cifereca-task",
        *, max_pending: int | None = None,
        shared_admission: SharedTaskAdmission | None = None,
        admission_scope: str | None = None,
    ):
        if max_pending is not None and (type(max_pending) is not int or max_pending < 1):
            raise ValueError("max_pending must be a positive integer")
        if shared_admission is not None and not isinstance(shared_admission, SharedTaskAdmission):
            raise ValueError("invalid shared admission")
        if admission_scope is not None and (
            type(admission_scope) is not str or
            not 1 <= len(admission_scope) <= 128 or shared_admission is None
        ):
            raise ValueError("invalid task scope")
        self._shared_admission = shared_admission
        self._admission_scope = admission_scope
        self._leases: dict[Future[Any], AdmissionLease] = {}
        self._max_pending = max_pending
        self._pending = 0
        self._active = 0
        self._admitted = 0
        self._rejected = 0
        # Bounded numeric timings; only pending futures have timestamps.
        self._queued_at: dict[Future[Any], int] = {}
        self._queue_wait_ns: deque[int] = deque(maxlen=256)
        self._worker_ns: deque[int] = deque(maxlen=256)
        self._queue: queue.PriorityQueue[tuple[int, int, Future[Any] | None, Callable[[], Any] | None]] = queue.PriorityQueue()
        self._sequence = itertools.count()
        self._closed = False
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        for index in range(max(1, int(max_workers))):
            thread = threading.Thread(target=self._worker, name=f"{thread_name_prefix}_{index}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def submit(self, priority: int | TaskPriority, fn: Callable[[], Any]) -> Future[Any]:
        # A None task has the same sentinel shape as shutdown. Never place
        # untrusted/malformed work into that queue: the worker would exit,
        # leaving its Future pending and any shared admission lease stranded.
        # Validate priority before holding the pool lock; do not invoke a
        # plugin-defined __int__ hook under that lock.
        if not callable(fn):
            raise TypeError("executor task must be callable")
        if type(priority) not in (int, TaskPriority):
            raise ValueError("invalid task priority")
        queued_priority = int(priority)
        with self._lock:
            if self._closed:
                raise RuntimeError("executor already closed")
            if self._max_pending is not None and self._pending >= self._max_pending:
                self._rejected += 1
                raise RuntimeError("executor pending queue full")
            future: Future[Any] = Future()
            if self._shared_admission is not None:
                lease = self._shared_admission.acquire(
                    priority=queued_priority, scope=self._admission_scope,
                )
                if lease is None:
                    self._rejected += 1
                    raise RuntimeError("shared task admission full")
                self._leases[future] = lease
            # Register for all futures, not only shared-admission tasks.
            # Cancellation removes a queued task from local pending capacity
            # immediately; a cancelled low-priority entry may otherwise
            # block a new user action while waiting behind other work.
            future.add_done_callback(self._release_cancelled_lease)
            self._queued_at[future] = monotonic_ns()
            self._queue.put((queued_priority, next(self._sequence), future, fn))
            self._pending += 1
            self._admitted += 1
            return future

    def _release_cancelled_lease(self, future: Future[Any]) -> None:
        if not future.cancelled():
            return
        # A worker and a caller cancelling a Future may race. The timestamp
        # is the single owner marker for pending accounting: whichever removes
        # it first decrements _pending exactly once. Shutdown drains this same
        # marker before cancelling removed futures outside the pool lock.
        with self._lock:
            if self._queued_at.pop(future, None) is not None:
                self._pending -= 1
        self._release_lease(future)

    def _release_lease(self, future: Future[Any]) -> None:
        with self._lock:
            lease = self._leases.pop(future, None)
        if lease is not None:
            lease.release()

    def admission_snapshot(self) -> dict[str, int | bool | None]:
        """Return only numeric local admission metrics (no task/user labels)."""
        with self._lock:
            return {
                "pending": self._pending,
                "active": self._active,
                "admitted": self._admitted,
                "rejected": self._rejected,
                "max_pending": self._max_pending,
                "closed": self._closed,
            }

    def timing_snapshot(self) -> dict[str, dict[str, float | int]]:
        """Local p50/p95 on up to 256 started/completed task durations.

        This does not measure GTK delivery or claim a global task budget.
        """
        with self._lock:
            waits = tuple(self._queue_wait_ns)
            workers = tuple(self._worker_ns)
        return {
            "queue_wait": {
                "samples": len(waits),
                "p50_ms": _bounded_percentile_ms(waits, 50),
                "p95_ms": _bounded_percentile_ms(waits, 95),
            },
            "worker": {
                "samples": len(workers),
                "p50_ms": _bounded_percentile_ms(workers, 50),
                "p95_ms": _bounded_percentile_ms(workers, 95),
            },
        }

    def _worker(self) -> None:
        while True:
            _priority, _seq, future, fn = self._queue.get()
            try:
                if future is None or fn is None:
                    return
                with self._lock:
                    queued_at = self._queued_at.pop(future, None)
                    if queued_at is not None:
                        self._pending -= 1
                if not future.set_running_or_notify_cancel():
                    self._release_lease(future)
                    continue
                started_at = monotonic_ns()
                with self._lock:
                    self._active += 1
                    if queued_at is not None:
                        self._queue_wait_ns.append(max(0, started_at - queued_at))
                    lease = self._leases.get(future)
                if lease is not None:
                    lease.mark_running()
                try:
                    try:
                        with diagnostic_task_scope():
                            result = fn()
                    except BaseException as exc:  # noqa: BLE001 - Future boundary
                        try:
                            future.set_exception(exc)
                        except BaseException:
                            # Future marks itself finished before invoking
                            # caller callbacks. A callback raising SystemExit
                            # or KeyboardInterrupt must not kill this worker.
                            # Never echo its potentially private message.
                            if not future.done():
                                raise
                    else:
                        try:
                            future.set_result(result)
                        except BaseException:
                            # Same boundary for successful task callbacks.
                            # A genuine incomplete Future is not swallowed.
                            if not future.done():
                                raise
                finally:
                    elapsed_ns = max(0, monotonic_ns() - started_at)
                    with self._lock:
                        self._active -= 1
                        self._worker_ns.append(elapsed_ns)
                    # set_result invokes user callbacks before worker exit.
                    # Only worker finally may free an active reservation.
                    self._release_lease(future)
            finally:
                self._queue.task_done()

    def shutdown(self, *, wait: bool = False, cancel_futures: bool = True) -> None:
        cancelled: list[Future[Any]] = []
        with self._lock:
            if not self._closed:
                self._closed = True
                if cancel_futures:
                    while True:
                        try:
                            _priority, _seq, future, _fn = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        if future is not None:
                            self._pending -= 1
                            self._queued_at.pop(future, None)
                            cancelled.append(future)
                        self._queue.task_done()
                for _ in self._threads:
                    # Valid public priorities are unbounded integers. A finite
                    # stop marker could overtake pending user work and strand
                    # Future results and SharedTaskAdmission leases.
                    self._queue.put((float("inf"), next(self._sequence), None, None))
        # A later shutdown(wait=True) must still join a pool that was
        # previously closed with wait=False. Closing is idempotent; waiting
        # for shutdown completion is an independent caller request.
        # Future.cancel() synchronously invokes arbitrary user callbacks.
        # Never call it under the pool lock: callbacks may inspect admission
        # metrics, submit more work or call shutdown themselves.
        for future in cancelled:
            try:
                future.cancel()
            except BaseException:
                # Future.cancel() sets CANCELLED before invoking callbacks.
                # A plugin callback raising SystemExit/KeyboardInterrupt must
                # not abandon other futures already removed from the queue.
                # Do not publish callback text; propagate only if cancellation
                # never actually happened.
                if not future.cancelled():
                    raise
        # Never synchronously join this executor from one of its workers.
        # Apart from a self-join RuntimeError, joining a *different* worker
        # can deadlock if that worker awaits completion of this task. The
        # stop sentinels are already queued; external callers may wait.
        if wait and threading.current_thread() not in self._threads:
            for thread in self._threads:
                thread.join()
