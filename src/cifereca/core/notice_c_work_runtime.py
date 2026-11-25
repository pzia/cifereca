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

    def __init__(self, storage, broker, *, clock=None, replay_registry=None, diagnostics=None,
                 capture_directory=None):
        if type(broker) is not TaskBrokerImpl or broker.max_workers < 3:
            raise ValueError("notice_work_invalid_runtime_broker")
        validate_notice_work_admission(broker.shared_admission)
        self._broker = broker
        self._storage = storage
        self._diagnostics = diagnostics
        self._capture_directory = capture_directory
        self._clock = clock
        self._replay_registry = replay_registry
        self._pending_replay_registry = None
        self._fetch_authority = None
        self._fetch_execution = None
        self._purchase_execution = None
        self._prepare_public_age = None
        self._public_age_digests = ()
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
            on_descriptive_committed=self._notify_descriptive,
                on_witness_rejected=self._notify_witness_rejected,
                capture_directory=self._capture_directory,
                prepare_public_age=self._prepare_public_age,
                public_age_digests=self._public_age_digests,
        )

    def _workers_active_locked(self):
        return self._progress["workers_submitted"] != sum(
            self._progress[key] for key in ("workers_current", "workers_retry",
                "workers_error", "workers_skipped", "workers_cancelled")
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
            if self._workers_active_locked():
                return False
            if type(registry) is not PublicReplayRegistry:
                raise ValueError("notice_reparse_invalid_registry")
            self._pump = PublicNoticeWorkPump(
                self._storage, self._broker, clock=self._clock,
                on_committed=self._notify, replay_registry=registry,
                fetch_execution=self._fetch_execution,
                purchase_execution=self._purchase_execution,
                on_purchase_committed=self._notify_purchase,
                on_descriptive_committed=self._notify_descriptive,
                on_witness_rejected=self._notify_witness_rejected,
                capture_directory=self._capture_directory,
                prepare_public_age=self._prepare_public_age,
                public_age_digests=self._public_age_digests,
            )
            self._replay_registry = registry
            self._pending_replay_registry = None
            return True

    def stage_replay_port(self, port, receipt):
        """Stage a qualified port for the next existing scan, not a job.

        A replacement never interrupts an admitted scan. In-flight workers
        retain their old port and must pass its session/input lease checks.
        Reuse a live port when it already covers this exact receipt.
        """
        from .notice_c_reparse_execution import PublicReplayRegistry, PublicReplayPort
        from .notice_c_work_bridge import RetryableNoticeWork, UnavailableNoticeWork

        from .notice_c_raw_input_receipt import _valid
        from .notice_c_reparse_execution import _match_input
        from .notice_c_reparse_requests import PublicReparseRequest

        if type(port) is not PublicReplayPort or not _valid(receipt):
            raise ValueError("notice_reparse_invalid_registry")
        if (port.parser_family_digest != receipt.parser_family_digest
                or receipt.producer_token is None):
            return False
        # Registration alone must not accept a stale or unrelated token.
        request = PublicReparseRequest(None, receipt, port.implementation_digest)
        try:
            _match_input(request, port.read_input(receipt.producer_token))
        except (RetryableNoticeWork, UnavailableNoticeWork):
            return False
        key = (port.provider_id, port.parser_family_digest, port.implementation_digest)
        with self._lock:
            if self._closed:
                return False
            registry = self._pending_replay_registry or self._replay_registry
            ports = () if registry is None else registry._ports
        for old in ports:
            if (old.provider_id, old.parser_family_digest, old.implementation_digest) == key:
                try:
                    _match_input(request, old.read_input(receipt.producer_token))
                except (RetryableNoticeWork, UnavailableNoticeWork):
                    continue
                # A previous staging wake may have been refused by admission.
                # Reuse the port but offer another nonblocking wake.
                self.request_tick()
                return True
        with self._lock:
            if self._closed:
                return False
            registry = self._pending_replay_registry or self._replay_registry
            ports = () if registry is None else registry._ports
            # Only one active implementation per provider/family in this
            # live registry. A previous implementation must not dispatch
            # fresh work under an obsolete version after replacement.
            ports = tuple(p for p in ports if
                          (p.provider_id, p.parser_family_digest) != key[:2])
            self._pending_replay_registry = PublicReplayRegistry(ports + (port,))
        self.request_tick()
        return True

    def register_search_replay(self, source, receipt, port):
        """Register exact live input and request replay only on version change.

        Normal Search with the current parser does not schedule duplicate
        parsing. An old retained input may be replayed by a newly qualified
        implementation of its family, with the existing journal CAS.
        """
        from .notice_c_reparse_requests import _spec
        from . import notice_c_job_journal as journal

        if port.provider_id != source.observation.key.provider_id:
            return None
        if not self.stage_replay_port(port, receipt):
            return None
        if port.implementation_digest == receipt.parser_implementation_digest:
            return None
        spec = _spec(source, receipt, port.implementation_digest)
        with self._storage._lock, self._storage._connect() as db:
            db.execute("BEGIN")
            previous = journal._load(db, journal._key(spec))
            generation = 0 if previous is None else previous.ref.generation
        # Any concurrent source, receipt, implementation or journal change
        # is refused by the normal request/worker fences. No new GET.
        return self.request_public_replay(
            source, receipt, implementation_digest=port.implementation_digest,
            expected_generation=generation,
        )

    def request_public_replay(self, source, receipt, *, implementation_digest,
                              expected_generation):
        """Trusted worker entry point; no network, new queue or parser grant.

        The caller selects an already admitted source and implementation.
        Read/hold the exact registered producer input before durable CAS.
        Dispatch independently rechecks the registry, receipt and lease.
        A concurrent runtime shutdown may leave a recoverable durable job.
        """
        from .notice_c_reparse_execution import (
            PublicReplayRegistry, _match_input,
        )
        from .notice_c_reparse_requests import (
            PublicReparseRequest, request_public_reparse,
        )
        from .notice_c_work_bridge import UnavailableNoticeWork

        with self._lock:
            registry = self._pending_replay_registry or self._replay_registry
            pump = self._pump
            if self._closed or type(registry) is not PublicReplayRegistry:
                raise UnavailableNoticeWork("notice_reparse_parser_unavailable")
        request = PublicReparseRequest(source, receipt, implementation_digest)
        port = registry.select(request)
        selected = port.read_input(receipt.producer_token)
        _match_input(request, selected)
        with port.hold_input(receipt.producer_token) as live:
            _match_input(request, live)
            if live != selected:
                raise UnavailableNoticeWork("notice_reparse_input_changed")
            with self._lock:
                if self._closed or (self._pending_replay_registry or self._replay_registry) is not registry:
                    raise UnavailableNoticeWork("notice_reparse_parser_unavailable")
            ref = request_public_reparse(
                self._storage, source, receipt, selected.body,
                implementation_digest=implementation_digest,
                now=pump._clock(), expected_generation=expected_generation,
            )
        self.request_tick()
        return ref

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
                on_descriptive_committed=self._notify_descriptive,
                on_witness_rejected=self._notify_witness_rejected,
                capture_directory=self._capture_directory,
                prepare_public_age=self._prepare_public_age,
                public_age_digests=self._public_age_digests,
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
                on_descriptive_committed=self._notify_descriptive,
                on_witness_rejected=self._notify_witness_rejected,
                capture_directory=self._capture_directory,
                prepare_public_age=self._prepare_public_age,
                public_age_digests=self._public_age_digests,
            )
            self._purchase_execution = execution
            return True

    def install_public_age_execution(self, prepare, *, implementation_digests):
        """Explicit trusted Core composition before the first heartbeat.

        No job or new broker is created here. The caller owns archive
        authority and must reattest it at preparation and publication.
        """
        with self._lock:
            if self._closed or self._dispatch_requested or self._prepare_public_age is not None:
                return False
            if not callable(prepare) or not implementation_digests:
                raise ValueError("notice_work_invalid_public_age_port")
            pump = PublicNoticeWorkPump(
                self._storage, self._broker, clock=self._clock,
                on_committed=self._notify, replay_registry=self._replay_registry,
                fetch_execution=self._fetch_execution, purchase_execution=self._purchase_execution,
                on_purchase_committed=self._notify_purchase,
                on_descriptive_committed=self._notify_descriptive,
                on_witness_rejected=self._notify_witness_rejected,
                capture_directory=self._capture_directory,
                prepare_public_age=prepare, public_age_digests=implementation_digests,
            )
            self._pump = pump
            self._prepare_public_age = prepare
            self._public_age_digests = implementation_digests
            return True

    def request_public_age(self, *, target_edition_id, scope, expected_generation,
                           group_document=None):
        """Explicit trusted Core producer, on an existing worker, never GTK.

        Scope applicability belongs to the caller. The exact public C and
        durable pin are reattested in the request transaction; the installed
        archive and SQL lineage are checked again by the admitted worker.
        No queue or attempt is allocated for an unsupported implementation.
        """
        from .notice_c_public_age_request import request_public_age_consolidation

        with self._lock:
            if self._closed or self._prepare_public_age is None:
                return None
            digests = self._public_age_digests
            clock = self._pump._clock
        ref = request_public_age_consolidation(
            self._storage, target_edition_id=target_edition_id, scope=scope,
            now=clock(), expected_generation=expected_generation,
            implementation_digests=digests, group_document=group_document,
        )
        if ref is not None:
            self.request_tick()
        return ref

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
        from .notice_parse_witness_alert import report_capture_diagnostic

        report_capture_diagnostic(
            self._diagnostics, diagnostic_id, capture_state, rejected=rejected,
        )

    def _notify_descriptive(self):
        # Age changes affect documentary consumers, not cover epochs.
        with self._lock:
            if not self._closed:
                self._documentary_revision += 1

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
            if self._pending_replay_registry is not None:
                # Do not reset the bridge's inflight dedup/limit while any
                # old worker is still outstanding. Do not dispatch more
                # obsolete work either. The last worker offers a fresh wake.
                if self._workers_active_locked():
                    self._progress["scans_completed"] += 1
                    self._progress["deferred_scans"] += 1
                    return DispatchTick((), 0, 0, True)
                self._replay_registry = self._pending_replay_registry
                self._pending_replay_registry = None
                self._pump = PublicNoticeWorkPump(
                    self._storage, self._broker, clock=self._clock,
                    on_committed=self._notify, replay_registry=self._replay_registry,
                    fetch_execution=self._fetch_execution,
                    purchase_execution=self._purchase_execution,
                    on_purchase_committed=self._notify_purchase,
                    on_descriptive_committed=self._notify_descriptive,
                    on_witness_rejected=self._notify_witness_rejected,
                    capture_directory=self._capture_directory,
                    prepare_public_age=self._prepare_public_age,
                    public_age_digests=self._public_age_digests,
                )
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
                ), directory=self._capture_directory, limit=2):
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
                if self._pending_replay_registry is not None and not self._workers_active_locked():
                    self.request_tick()

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
            self._pending_replay_registry = None
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
