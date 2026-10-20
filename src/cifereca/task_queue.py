from __future__ import annotations

import itertools
import queue
import threading
from concurrent.futures import Future
from enum import IntEnum
from typing import Any, Callable

from .diagnostic_task_context import diagnostic_task_scope


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

    def __init__(self, max_workers: int = 3, thread_name_prefix: str = "cifereca-task", *, max_pending: int | None = None):
        if max_pending is not None and (type(max_pending) is not int or max_pending < 1):
            raise ValueError("max_pending must be a positive integer")
        self._max_pending = max_pending
        self._pending = 0
        self._active = 0
        self._admitted = 0
        self._rejected = 0
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
        with self._lock:
            if self._closed:
                raise RuntimeError("executor already closed")
            if self._max_pending is not None and self._pending >= self._max_pending:
                self._rejected += 1
                raise RuntimeError("executor pending queue full")
            future: Future[Any] = Future()
            self._queue.put((int(priority), next(self._sequence), future, fn))
            self._pending += 1
            self._admitted += 1
            return future

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

    def _worker(self) -> None:
        while True:
            _priority, _seq, future, fn = self._queue.get()
            try:
                if future is None or fn is None:
                    return
                with self._lock:
                    self._pending -= 1
                if not future.set_running_or_notify_cancel():
                    continue
                with self._lock:
                    self._active += 1
                try:
                    try:
                        with diagnostic_task_scope():
                            result = fn()
                    except BaseException as exc:  # noqa: BLE001 - Future boundary
                        future.set_exception(exc)
                    else:
                        future.set_result(result)
                finally:
                    with self._lock:
                        self._active -= 1
            finally:
                self._queue.task_done()

    def shutdown(self, *, wait: bool = False, cancel_futures: bool = True) -> None:
        cancelled: list[Future[Any]] = []
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if cancel_futures:
                while True:
                    try:
                        _priority, _seq, future, _fn = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if future is not None:
                        self._pending -= 1
                        cancelled.append(future)
                    self._queue.task_done()
            for _ in self._threads:
                self._queue.put((10_000, next(self._sequence), None, None))
        # Future.cancel() synchronously invokes arbitrary user callbacks.
        # Never call it under the pool lock: callbacks may inspect admission
        # metrics, submit more work or call shutdown themselves.
        for future in cancelled:
            future.cancel()
        # Never synchronously join this executor from one of its workers.
        # Apart from a self-join RuntimeError, joining a *different* worker
        # can deadlock if that worker awaits completion of this task. The
        # stop sentinels are already queued; external callers may wait.
        if wait and threading.current_thread() not in self._threads:
            for thread in self._threads:
                thread.join()
