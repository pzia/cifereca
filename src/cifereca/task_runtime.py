from __future__ import annotations

from concurrent.futures import CancelledError as FutureCancelledError, Future
import itertools
import logging
import threading
import time
from typing import Any, Callable

from cifereca.plugin_api import Cancelled, TaskControl, TaskHandle
from .task_queue import PriorityExecutor
from .task_admission import SharedTaskAdmission


_TRACE_LOG = logging.getLogger("cifereca")


class _TaskControl:
    def __init__(
        self,
        on_progress: Callable[[int | None, int | None, str], None] | None = None,
    ) -> None:
        self._cancelled = threading.Event()
        self._on_progress = on_progress

    def cancel(self) -> None:
        self._cancelled.set()

    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise Cancelled("task cancelled")

    def report(
        self,
        *,
        current: int | None = None,
        total: int | None = None,
        message: str = "",
    ) -> None:
        if self.cancelled() or self._on_progress is None:
            return
        try:
            self._on_progress(current, total, str(message or ""))
        except Exception as exc:
            _TRACE_LOG.warning(
                "TRACE plugin-task progress-callback-error error=%s",
                type(exc).__name__,
            )


class _TaskHandle:
    def __init__(self, future: Future[Any], control: _TaskControl) -> None:
        self._future = future
        self._control = control

    @property
    def control(self) -> TaskControl:
        return self._control

    def cancel(self) -> None:
        self._control.cancel()
        self._future.cancel()

    def done(self) -> bool:
        return self._future.done()

    def result(self, timeout: float | None = None) -> Any:
        try:
            return self._future.result(timeout=timeout)
        except FutureCancelledError as exc:
            raise Cancelled("task cancelled") from exc

    def add_done_callback(self, callback: Callable[[TaskHandle], None]) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._future.add_done_callback(lambda _future: callback(self))


class _ScopedTaskBroker:
    def __init__(self, owner: "TaskBrokerImpl", plugin_id: str) -> None:
        self._owner = owner
        self._plugin_id = str(plugin_id or "plugin")

    def submit(
        self,
        operation: Callable[[TaskControl], Any],
        *,
        priority: int = 40,
        label: str = "",
        kind: str = "plugin",
        on_progress: Callable[[int | None, int | None, str], None] | None = None,
    ) -> TaskHandle:
        return self._owner._submit(
            self._plugin_id,
            operation,
            priority=priority,
            label=label,
            kind=kind,
            on_progress=on_progress,
        )


class TaskBrokerImpl:
    """Bounded Core-owned executor exposed to plugins through a narrow broker."""

    def __init__(
        self, service: Any, *, max_workers: int = 3,
        max_pending: int | None = None,
        shared_admission: SharedTaskAdmission | None = None,
        admission_scope: str | None = None,
    ) -> None:
        if max_pending is not None and (type(max_pending) is not int or max_pending < 1):
            raise ValueError("max_pending must be a positive integer")
        self.service = service
        self.max_workers = max(1, int(max_workers))
        self.max_pending = max_pending
        self.shared_admission = shared_admission
        self.admission_scope = admission_scope
        self._executor: PriorityExecutor | None = None
        self._lock = threading.RLock()
        self._closed = False
        self._ids = itertools.count(1)
        self._controls: dict[int, _TaskControl] = {}

    def for_plugin(self, plugin_id: str, *, trusted: bool = False) -> _ScopedTaskBroker:
        del trusted
        return _ScopedTaskBroker(self, plugin_id)

    def submit(
        self,
        operation: Callable[[TaskControl], Any],
        *,
        priority: int = 40,
        label: str = "",
        kind: str = "core",
        on_progress: Callable[[int | None, int | None, str], None] | None = None,
    ) -> TaskHandle:
        return self._submit(
            "core",
            operation,
            priority=priority,
            label=label,
            kind=kind,
            on_progress=on_progress,
        )

    def _submit(
        self,
        plugin_id: str,
        operation: Callable[[TaskControl], Any],
        *,
        priority: int,
        label: str,
        kind: str,
        on_progress: Callable[[int | None, int | None, str], None] | None,
    ) -> TaskHandle:
        if not callable(operation):
            raise TypeError("operation must be callable")
        # A plugin may control its advertised priority. Do not coerce bool,
        # float, numeric strings, or arbitrary __int__ objects into a trusted
        # user-action lane. Reject before allocating controls/executor work.
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise ValueError("invalid task priority")
        task_id = next(self._ids)
        control = _TaskControl(on_progress)
        queued_at = time.monotonic()

        # A plugin controls its public labels and kind and can also supply a
        # free-form scoped ID. None of these is trusted for persistent logs:
        # a label or ID may contain bearer URLs, local paths or account names.
        # Keep the original values for execution, without reflecting them in
        # the Core TRACE transport (#981 / #1741).
        trace_owner = "core" if plugin_id == "core" else "plugin"
        trace_kind = kind if type(kind) is str and kind in {
            "core", "plugin", "task", "source-sync", "opening-sync",
            "deep-sync", "sync-preflight", "kobo-acquisition", "maintenance",
            "library", "recent", "search", "other",
        } else "other"

        def invoke() -> Any:
            control.check_cancelled()
            started = time.monotonic()
            _TRACE_LOG.info(
                "TRACE plugin-task begin id=%s plugin=%s priority=%s kind=%s queue_wait=%.3fs",
                task_id, trace_owner, int(priority), trace_kind,
                started - queued_at,
            )
            outcome = "success"
            try:
                return operation(control)
            except Cancelled:
                outcome = "cancelled"
                raise
            except BaseException:
                outcome = "failed"
                raise
            finally:
                _TRACE_LOG.info(
                    "TRACE plugin-task end id=%s plugin=%s kind=%s status=%s dt=%.3fs",
                    task_id, trace_owner, trace_kind, outcome,
                    time.monotonic() - started,
                )

        with self._lock:
            if self._closed:
                raise RuntimeError("task broker already closed")
            if self._executor is None:
                self._executor = PriorityExecutor(
                    max_workers=self.max_workers,
                    thread_name_prefix="cifereca-plugin-task",
                    max_pending=self.max_pending,
                    shared_admission=self.shared_admission,
                    admission_scope=self.admission_scope,
                )
            executor = self._executor
            self._controls[task_id] = control
            try:
                future = executor.submit(priority, invoke)
            except Exception:
                self._controls.pop(task_id, None)
                raise

        handle = _TaskHandle(future, control)

        def cleanup(_future: Future[Any]) -> None:
            with self._lock:
                self._controls.pop(task_id, None)

        future.add_done_callback(cleanup)
        return handle

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            controls = tuple(self._controls.values())
            self._controls.clear()
            executor = self._executor
            self._executor = None
        for control in controls:
            control.cancel()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


__all__ = ["TaskBrokerImpl"]
