"""#4982 opt-in ownership of the public Notice C pump by the Core graph.

No timer, pool, implicit startup scan, provider dispatch or plugin API.
One explicit heartbeat submission may be outstanding. It scans one page
inside the already admitted maintenance worker, submits without waiting, and
returns; aggregate work uses the very same broker and shared admission.
"""
from __future__ import annotations

from threading import RLock

from ..task_admission import SharedTaskAdmission
from ..task_queue import TaskPriority
from ..task_runtime import TaskBrokerImpl
from .notice_c_work_pump import PublicNoticeWorkPump
from .notice_c_fetch_execution import PublicFetchExecution


def validate_notice_work_admission(admission):
    # At least two background reservations (scan + aggregate) and one
    # user-priority reservation. A caller-supplied per-scope ceiling must
    # not make that combination impossible in the single Core task broker.
    if (type(admission) is not SharedTaskAdmission
            or admission.reserve_user_slots < 1
            or admission.max_inflight - admission.reserve_user_slots < 2
            or (admission.max_per_scope is not None
                and admission.max_per_scope < 3)):
        raise ValueError("notice_work_invalid_runtime_admission")


class NoticeWorkRuntime:
    """Private lifecycle adapter; composition root owns the existing broker."""

    def __init__(self, storage, broker, *, clock=None, replay_registry=None):
        if type(broker) is not TaskBrokerImpl or broker.max_workers < 3:
            raise ValueError("notice_work_invalid_runtime_broker")
        validate_notice_work_admission(broker.shared_admission)
        self._broker = broker
        self._storage = storage
        self._clock = clock
        self._replay_registry = replay_registry
        self._fetch_authority = None
        self._dispatch_requested = False
        self._lock = RLock()
        self._closed = False
        self._scan = None
        self._observer = None
        self._pump = PublicNoticeWorkPump(
            storage, broker, clock=clock, on_committed=self._notify,
            replay_registry=replay_registry,
        )

    def install_fetch_execution(self, execution):
        """Trusted Core composition only, once, before any heartbeat hint.

        The initial inert pump has never scanned or admitted work. Replace
        that configuration before dispatch; never create a second active
        pump or broker. The caller transfers lifetime ownership of the
        authority only on success. Installation does not grant permission,
        request a job, open a session or initiate transport.
        """
        with self._lock:
            if self._closed or self._dispatch_requested or self._fetch_authority is not None:
                return False
            if (type(execution) is not PublicFetchExecution
                    or execution._authority._storage is not self._storage
                    or not execution.supported()):
                raise ValueError("notice_fetch_invalid_registry")
            self._pump = PublicNoticeWorkPump(
                self._storage, self._broker, clock=self._clock,
                on_committed=self._notify, replay_registry=self._replay_registry,
                fetch_execution=execution,
            )
            self._fetch_authority = execution._authority
            return True

    def bind_committed_observer(self, observer):
        """Install one thread-safe owner callback; None detaches it.

        This is not a plugin capability. The callback runs after commit
        on the Core worker. An already running callback may finish after
        detach; its consumer must handle shutdown (as the N2 service does).
        """
        if observer is not None and not callable(observer):
            raise ValueError("notice_work_invalid_observer")
        with self._lock:
            if self._closed:
                return False
            self._observer = observer
            return True

    def _notify(self, document, epoch):
        with self._lock:
            observer = None if self._closed else self._observer
        if observer is not None:
            observer(document, epoch)

    def request_tick(self):
        """Nonblocking heartbeat hint; no SQLite on the submitting thread.

        Calls may be made by an existing lifecycle coordinator. No new
        periodic mechanism is installed here. Saturation/duplicate/closed
        return None, leaving the durable journal untouched by this hint.
        """
        with self._lock:
            if self._closed or self._scan is not None:
                return None
            self._dispatch_requested = True
            try:
                handle = self._broker.submit(
                    self._scan_once, priority=TaskPriority.MAINTENANCE,
                    label="notice-c-dispatch", kind="maintenance",
                )
            except RuntimeError as exc:
                if str(exc) in {
                    "executor pending queue full", "shared task admission full",
                    "task broker already closed", "executor already closed",
                }:
                    return None
                raise
            self._scan = handle
            handle.add_done_callback(self._scan_finished)
            return handle

    def _scan_once(self, control):
        control.check_cancelled()
        with self._lock:
            if self._closed:
                return None
        return self._pump.tick(control=control)

    def _scan_finished(self, handle):
        with self._lock:
            if self._scan is handle:
                self._scan = None

    def close(self):
        """Stop new scans/notifications. The Core closes its broker next."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            handle = self._scan
            self._scan = None
            self._observer = None
            authority = self._fetch_authority
            self._fetch_authority = None
        if handle is not None:
            handle.cancel()
        if authority is not None:
            authority.close()
