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
    """Small bounded priority executor independent from GTK."""

    def __init__(self, max_workers: int = 3, thread_name_prefix: str = "cifereca-task"):
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
            future: Future[Any] = Future()
            self._queue.put((int(priority), next(self._sequence), future, fn))
            return future

    def _worker(self) -> None:
        while True:
            _priority, _seq, future, fn = self._queue.get()
            try:
                if future is None or fn is None:
                    return
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    with diagnostic_task_scope():
                        result = fn()
                except BaseException as exc:  # noqa: BLE001 - Future boundary
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            finally:
                self._queue.task_done()

    def shutdown(self, *, wait: bool = False, cancel_futures: bool = True) -> None:
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
                        future.cancel()
                    self._queue.task_done()
            for _ in self._threads:
                self._queue.put((10_000, next(self._sequence), None, None))
        if wait:
            for thread in self._threads:
                thread.join()
