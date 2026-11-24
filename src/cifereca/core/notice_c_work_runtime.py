"""#4982 opt-in ownership of the public Notice C pump by the Core graph.

No timer, pool, implicit startup scan, provider dispatch or plugin API.
One explicit heartbeat submission may be outstanding. It scans one page
inside the already admitted maintenance worker, submits without waiting, and
returns; aggregate work uses the very same broker and shared admission.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from ..plugin_api import Cancelled
from ..task_admission import SharedTaskAdmission
from ..task_queue import TaskPriority
from ..task_runtime import TaskBrokerImpl
from .notice_c_work_pump import DispatchTick, PublicNoticeWorkPump
from .notice_c_fetch_execution import PublicFetchExecution
from .notice_c_purchase_execution import PurchaseIntentExecution
from .notice_regression_capture_intent import resume_captures
from .notice_c_reparse_execution import resolve_replay_capture_members


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


@dataclass(frozen=True, slots=True)
class NoticeWorkProgress:
    """Process-local documentary dispatch counters, not an inventory total.

    A scan may be partial or deferred; a worker reporting current need not
    have changed the canonical document. Nothing here represents downloads,
    acquisitions, eligible backlog size or an exhaustive completion percentage.
    """

    scan_active: bool
    hint_pending: bool
    documentary_revision: int
    scans_completed: int
    scans_failed: int
    scans_cancelled: int
    admission_refusals: int
    rows_examined: int
    corrupt_rows: int
    deferred_scans: int
    workers_submitted: int
    workers_current: int
    workers_retry: int
    workers_error: int
    workers_skipped: int
    workers_cancelled: int

    @property
    def workers_running_or_queued(self):
        return self.workers_submitted - (
            self.workers_current + self.workers_retry + self.workers_error
            + self.workers_skipped + self.workers_cancelled
        )


class NoticeWorkRuntime:
    """Private lifecycle adapter; composition root owns the existing broker."""

    def __init__(self, storage, broker, *, clock=None, replay_registry=None, diagnostics=None):
        if type(broker) is not TaskBrokerImpl or broker.max_workers < 3:
            raise ValueError("notice_work_invalid_runtime_broker")
        validate_notice_work_admission(broker.shared_admission)
        self._broker = broker
        self._storage = storage
        self._diagnostics = diagnostics
        self._clock = clock
        self._replay_registry = replay_registry
        self._fetch_authority = None
        self._fetch_execution = None
        self._purchase_execution = None
        self._dispatch_requested = False
        self._lock = RLock()
        self._closed = False
        self._scan = None
        self._hint_pending = False
        self._observer = None
        self._documentary_revision = 0
        self._progress = dict.fromkeys((
            "scans_completed", "scans_failed", "scans_cancelled",
            "admission_refusals", "rows_examined", "corrupt_rows",
            "deferred_scans", "workers_submitted", "workers_current",
            "workers_retry", "workers_error", "workers_skipped",
            "workers_cancelled",
        ), 0)
        self._pump = PublicNoticeWorkPump(
            storage, broker, clock=clock, on_committed=self._notify,
            replay_registry=replay_registry,
            on_purchase_committed=self._notify_purchase,
                on_witness_rejected=self._notify_witness_rejected,
        )

    def install_replay_registry(self, registry):
        """Install qualified replay ports at an idle dispatch boundary.

        Session owners retain responsibility for revocation through the
        read/hold callbacks. No transport or work request is initiated here.
        """
        from .notice_c_reparse_execution import PublicReplayRegistry

        with self._lock:
            if self._closed or self._scan is not None or self._hint_pending:
                return False
            if self._progress["workers_submitted"] != sum(
                    self._progress[key] for key in ("workers_current", "workers_retry",
                    "workers_error", "workers_skipped", "workers_cancelled")):
                return False
            if type(registry) is not PublicReplayRegistry:
                raise ValueError("notice_reparse_invalid_registry")
            self._pump = PublicNoticeWorkPump(
                self._storage, self._broker, clock=self._clock,
                on_committed=self._notify, replay_registry=registry,
                fetch_execution=self._fetch_execution,
                purchase_execution=self._purchase_execution,
                on_purchase_committed=self._notify_purchase,
                on_witness_rejected=self._notify_witness_rejected,
            )
            self._replay_registry = registry
            return True

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
                fetch_execution=execution, purchase_execution=self._purchase_execution,
                on_purchase_committed=self._notify_purchase,
                on_witness_rejected=self._notify_witness_rejected,
            )
            self._fetch_execution = execution
            self._fetch_authority = execution._authority
            return True

    def install_purchase_execution(self, execution):
        """Explicit Core composition before the first heartbeat; same pump."""
        with self._lock:
            if self._closed or self._dispatch_requested or self._purchase_execution is not None:
                return False
            if (type(execution) is not PurchaseIntentExecution
                    or execution._storage is not self._storage):
                raise ValueError("notice_purchase_invalid_registry")
            self._pump = PublicNoticeWorkPump(
                self._storage, self._broker, clock=self._clock,
                on_committed=self._notify, replay_registry=self._replay_registry,
                fetch_execution=self._fetch_execution, purchase_execution=execution,
                on_purchase_committed=self._notify_purchase,
                on_witness_rejected=self._notify_witness_rejected,
            )
            self._purchase_execution = execution
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

    def documentary_revision(self):
        """Cheap in-memory invalidation watermark; never a source credential."""
        with self._lock:
            return None if self._closed else self._documentary_revision

    def progress_snapshot(self):
        """Nonblocking read of this runtime only; no SQLite, transport or GTK."""
        with self._lock:
            if self._closed:
                return None
            return NoticeWorkProgress(
                scan_active=self._scan is not None,
                hint_pending=self._hint_pending,
                documentary_revision=self._documentary_revision,
                **self._progress,
            )

    def _notify_witness_rejected(self, diagnostic_id, capture_state):
        self._report_capture(diagnostic_id, capture_state, rejected=True)

    def _report_capture(self, diagnostic_id, capture_state, *, rejected=False):
        if self._diagnostics is None:
            return
        # Existing canonical Diagnostics journal; no new UI or log store.
        # Only a closed Core outcome crosses this boundary.
        if capture_state not in ("pending", "complete", "partial", "failed",
                                 "capacity_refused", "metadata_unavailable"):
            return
        try:
            self._diagnostics.add(
                "NOTICE", "parser_witness_rejected" if rejected else "regression_capture_resumed",
                "warning" if rejected else ("error" if capture_state == "failed"
                                             else "partial" if capture_state == "partial" else "ok"),
                detail={"diagnostic_id": diagnostic_id, "capture_state": capture_state},
            )
        except Exception:
            pass

    def _notify_purchase(self):
        with self._lock:
            if not self._closed:
                self._documentary_revision += 1

    def _notify(self, document, epoch):
        with self._lock:
            if not self._closed:
                self._documentary_revision += 1
            observer = None if self._closed else self._observer
        if observer is not None:
            observer(document, epoch)

    def recover_search_sources(self, sources, *, now):
        """Recover failed live C publication on the existing Search worker.

        Normal publication and reconciliation run first. Established dirty
        relations, a missing exact self-C, initial group or strict group
        extension qualify; no GET or extra queue.
        """
        from sqlite3 import Error as SQLiteError
        from .notice_c_work_requests import request_affected_public_recompute
        from .notice_c_initial_publication import request_initial_publication
        from .notice_c_initial_group_publication import request_search_group_repair

        if type(sources) is not tuple or not 1 <= len(sources) <= 32:
            raise ValueError("notice_work_invalid_repair_batch")
        requested = 0
        for source in sources:
            with self._lock:
                if self._closed:
                    return requested
            try:
                result = request_affected_public_recompute(
                    self._storage, source, now=now,
                )
            except (ValueError, TypeError, SQLiteError):
                continue
            except OSError:
                # An isolated publication failure need not suppress a
                # different repair transaction. Each port reattests source.
                pass
            else:
                requested += len(result.refs)
            try:
                first = request_initial_publication(self._storage, source, now=now)
            except (ValueError, TypeError, SQLiteError):
                continue
            except OSError:
                pass
            else:
                requested += int(first is not None)
            try:
                group = request_search_group_repair(self._storage, source, now=now)
            except (ValueError, TypeError, OSError, SQLiteError):
                continue
            requested += int(group is not None)
        if requested:
            self.request_tick()
        return requested

    def request_tick(self):
        """Nonblocking heartbeat hint; no SQLite on the submitting thread.

        Calls may be made by an existing lifecycle coordinator. No new
        periodic mechanism is installed here. A hint arriving during an
        in-flight scan coalesces into one follow-up submission; admission
        refusal preserves the hint for the next external wake, never spins.
        """
        with self._lock:
            if self._closed:
                return None
            self._hint_pending = True
            if self._scan is not None:
                return None
            self._dispatch_requested = True
            self._hint_pending = False
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
                    self._hint_pending = True
                    self._progress["admission_refusals"] += 1
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
            pump = self._pump
            registry = self._replay_registry
        outcome = pump.tick(control=control)
        if type(outcome) is DispatchTick:
            with self._lock:
                if not self._closed:
                    self._progress["scans_completed"] += 1
                    self._progress["rows_examined"] += outcome.examined
                    self._progress["corrupt_rows"] += outcome.corrupt
                    self._progress["deferred_scans"] += int(outcome.deferred)
                    self._progress["workers_submitted"] += len(outcome.handles)
                    # Add all submissions before a callback can run inline
                    # for an already completed Future. No new work queue.
                    for worker in outcome.handles:
                        worker.add_done_callback(self._worker_finished)
        # Existing admitted maintenance worker, no extra pool or network.
        # Durable snapshots suffice when original bytes cannot be exported.
        if control.cancelled():
            return outcome
        for diagnostic, state in resume_captures(
                self._storage, lambda diagnostic: resolve_replay_capture_members(
                    self._storage, registry, diagnostic, clock=pump._clock,
                ), limit=2):
            self._report_capture(diagnostic, state)
        return outcome

    def _worker_finished(self, handle):
        # The callback only runs on a completed Future; never waits for
        # another worker. Count the bridge's closed outcomes, not attempts
        # or canonical rows, and never reflect arbitrary exception text.
        try:
            outcome = handle.result()
        except Cancelled:
            counter = "workers_cancelled"
        except Exception:
            counter = "workers_error"
        else:
            counter = {
                "current": "workers_current",
                "transient_failure": "workers_retry",
                "error": "workers_error",
            }.get(outcome, "workers_skipped") if type(outcome) is str else "workers_error"
        with self._lock:
            if not self._closed:
                self._progress[counter] += 1

    def _scan_finished(self, handle):
        with self._lock:
            if self._scan is not handle:
                return
            self._scan = None
            try:
                handle.result()
            except Cancelled:
                self._progress["scans_cancelled"] += 1
            except Exception:
                self._progress["scans_failed"] += 1
            # The old scan may still hold its admission until its worker
            # exits. A refusal is nonblocking and does not consume a job.
            if self._hint_pending and not self._closed:
                self.request_tick()

    def close(self):
        """Stop new scans/notifications. The Core closes its broker next."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            handle = self._scan
            self._scan = None
            self._hint_pending = False
            self._observer = None
            authority = self._fetch_authority
            self._fetch_authority = None
            self._fetch_execution = None
            purchase = self._purchase_execution
            self._purchase_execution = None
        if handle is not None:
            handle.cancel()
        if authority is not None:
            authority.close()
        if purchase is not None:
            purchase.close()
