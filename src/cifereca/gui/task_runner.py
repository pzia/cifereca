from __future__ import annotations

import itertools
import logging
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable

from ..task_queue import PriorityExecutor, TaskPriority
from ..plugin_api import AttentionRequired, UserFacingError

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

_TRACE_LOG = logging.getLogger("cifereca")


class TaskRunner:
    """Run non-UI operations outside GTK with priority and global feedback."""

    def __init__(self, window: Any, *, max_workers: int = 3):
        self.window = window
        self.pool = PriorityExecutor(max_workers=max_workers, thread_name_prefix="cifereca-task")
        self._count = 0
        self._lock = threading.Lock()
        self._task_ids = itertools.count(1)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    def submit(
        self,
        label: str,
        fn: Callable[[], Any],
        ok: Callable[[Any], None] | None = None,
        err: Callable[[BaseException], None] | None = None,
        control: Gtk.Widget | None = None,
        success_toast: str | None = None,
        *,
        priority: TaskPriority = TaskPriority.NORMAL,
        kind: str = "task",
    ) -> None:
        # A late plugin/action must not disable a detached GTK control or
        # enqueue a worker after the window owner has been torn down.
        if getattr(self.window, "_closed", False):
            return
        # A navigation/rebuild may have detached the caller's widget even
        # though the window is still open. Do not touch orphan GTK controls;
        # the work itself remains valid and callbacks are handled separately.
        if control is not None and control.get_root() is not None:
            control.set_sensitive(False)
        task_id = next(self._task_ids)
        with self._lock:
            self._count += 1
            count = self._count
        GLib.idle_add(self.window._task_started, task_id, count, label, int(priority), kind)

        queued_at = time.monotonic()
        _TRACE_LOG.info(
            "TRACE task submit id=%s priority=%s kind=%s label=%s",
            task_id, int(priority), kind, label,
        )

        def traced_fn() -> Any:
            started = time.monotonic()
            _TRACE_LOG.info(
                "TRACE task begin id=%s priority=%s kind=%s queue_wait=%.3fs label=%s",
                task_id, int(priority), kind, started - queued_at, label,
            )
            try:
                return fn()
            finally:
                _TRACE_LOG.info(
                    "TRACE task end id=%s kind=%s dt=%.3fs label=%s",
                    task_id, kind, time.monotonic() - started, label,
                )

        try:
            future = self.pool.submit(priority, traced_fn)
        except RuntimeError:
            # close() can shut down PriorityExecutor between the window's
            # initial closed gate and enqueue. Balance the activity counter
            # and release the control; never publish exception internals.
            with self._lock:
                self._count = max(0, self._count - 1)
                remaining = self._count

            def rejected() -> bool:
                if getattr(self.window, "_closed", False):
                    return False
                if control is not None and control.get_root() is not None:
                    control.set_sensitive(True)
                self.window._task_finished(
                    task_id, remaining, label, False, "Traitement indisponible",
                )
                failure = RuntimeError("Traitement indisponible")
                if err:
                    err(failure)
                else:
                    self.present_error(failure)
                return False

            GLib.idle_add(rejected)
            return

        def done(fut: Future[Any]) -> None:
            try:
                value = fut.result()
                outcome: tuple[str, Any] = ("ok", value)
            except BaseException as exc:  # noqa: BLE001 - thread/UI boundary
                outcome = ("error", exc)

            def deliver() -> bool:
                with self._lock:
                    self._count = max(0, self._count - 1)
                    remaining = self._count
                if getattr(self.window, '_closed', False):
                    return False
                if control is not None and control.get_root() is not None:
                    control.set_sensitive(True)
                kind_outcome, payload = outcome
                self.window._task_finished(
                    task_id,
                    remaining,
                    label,
                    kind_outcome == "ok",
                    None if kind_outcome == "ok" else str(payload),
                )
                if kind_outcome == "ok":
                    if ok:
                        ok(payload)
                    if getattr(self.window, "_closed", False):
                        return False
                    # A success callback may close the window itself.
                    # Never update detached GTK feedback after that callback.
                    if success_toast and not getattr(self.window, "_closed", False):
                        reporter = getattr(self.window, "_activity_result", None)
                        if callable(reporter):
                            reporter(success_toast, success=True, task_id=task_id)
                        else:
                            self.window.toast(success_toast)
                else:
                    if getattr(self.window, "_closed", False):
                        return False
                    if err:
                        err(payload)
                    else:
                        self.present_error(payload)
                return False

            GLib.idle_add(deliver)

        future.add_done_callback(done)

    def present_error(self, payload: BaseException) -> None:
        if isinstance(payload, AttentionRequired):
            _TRACE_LOG.warning(
                "TRACE attention-required code=%s technical_detail=%s",
                payload.code, payload.technical_detail,
            )
            self.window.show_attention(payload.user_message, code=payload.code)
        elif isinstance(payload, UserFacingError):
            _TRACE_LOG.warning(
                "TRACE user-facing-error code=%s technical_detail=%s",
                payload.code, payload.technical_detail,
            )
            self.window.show_error(payload.user_message)
        else:
            self.window.show_error(str(payload))
