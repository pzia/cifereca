from __future__ import annotations

import itertools
import logging
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable

from ..task_queue import PriorityExecutor, TaskPriority
from ..task_admission import SharedTaskAdmission
from ..plugin_api import AttentionRequired, UserFacingError

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

_TRACE_LOG = logging.getLogger("cifereca")



class TaskRunner:
    """Run non-UI operations outside GTK with priority and global feedback."""

    @staticmethod
    def _task_has_typed_lock_busy(error: BaseException) -> bool:
        """Only a genuine LockBusyError (possibly chained) proves lock contention.
    
        Error messages, provider text and a lone BlockingIOError are insufficient.
        Avoid unbounded or cyclic exception chains.
        """
        from cifereca.locking import LockBusyError
        visited: set[int] = set()
        current: BaseException | None = error
        for _ in range(6):
            if current is None or id(current) in visited:
                break
            visited.add(id(current))
            if isinstance(current, LockBusyError):
                return True
            current = getattr(current, "__cause__", None)
        return False
    
    
    @staticmethod
    def _task_feedback_classification(origin: str, kind: str, control: object) -> str:
        """Closed, non-sensitive attribution from the submitted task contract."""
        if origin == "user" or control is not None:
            return "user_action"
        if kind == "provider-connect":
            return "provider_connect"
        if kind in {
            "opening-sync", "deep-sync", "source-sync", "source-materialize",
            "sync-preflight", "sync-audit",
        }:
            return "sync"
        return "background_task"


    def __init__(
        self, window: Any, *, max_workers: int = 3,
        max_pending: int | None = None,
        shared_admission: SharedTaskAdmission | None = None,
        admission_scope: str | None = None,
    ):
        self.window = window
        self.pool = PriorityExecutor(
            max_workers=max_workers, thread_name_prefix="cifereca-task",
            max_pending=max_pending,
            shared_admission=shared_admission,
            admission_scope=admission_scope,
        )
        self._count = 0
        self._lock = threading.Lock()
        self._task_ids = itertools.count(1)
        # Only explicitly marked, identical *automatic* work is coalesced.
        # Every key is scoped by task kind; user controls always bypass it.
        self._coalesced: dict[tuple, int] = {}

    @staticmethod
    def _source_sync_outcome(value: Any, kind: str) -> str:
        """Typed broker result, never arbitrary plugin truthiness (#2888).

        The result still travels through the success callback to refresh the
        UI. Only the final activity/TRACE receipt changes: a returned refusal
        is not a completed synchronization. Do not infer success from GET.
        """
        if kind in {"opening-sync", "deep-sync"}:
            # One opening task spans multiple identities/sources. Only the
            # Core's closed per-identity terminal statuses are aggregated;
            # never attribute a global partial to a Kobo download or infer
            # a GET from rights/pending counters.
            if type(value) is not dict or type(value.get("identities")) is not list:
                return "success"  # Legacy untyped workers unchanged.
            rows = value["identities"]
            if not rows:
                return "success"
            if len(rows) > 64 or any(
                type(row) is not dict
                or row.get("status") not in {"success", "partial", "failed", "cancelled"}
                for row in rows
            ):
                return "failed"
            statuses = {row["status"] for row in rows}
            if "failed" in statuses:
                return "failed"
            if "cancelled" in statuses:
                return "cancelled"
            return "partial" if "partial" in statuses else "success"
        if kind != "source-sync":
            return "success"
        from cifereca.plugin_api import SyncResult
        from collections.abc import Mapping
        if not isinstance(value, SyncResult):
            # A stale GTK source-action scope returns None before the Core is
            # called. Completing that worker is not proof of a source Sync;
            # never mint a positive task receipt or provider event for it.
            return "cancelled" if value is None else "success"
        facts = value.facts
        if isinstance(facts, Mapping):
            reason = facts.get("reason")
            status = facts.get("status")
            if reason == "operation_busy" or status == "operation_busy":
                return "busy"
            if status == "cancelled" or reason in {"cancelled", "source_sync_cancelled"}:
                return "cancelled"
            # A Core A→B summary is an observed result, not a GET error.
            # Validate the closed counters before distinguishing pending/blocked
            # from an actual delivery failure. A partial inventory alone gives
            # no proof of a failed download.
            execution = facts.get("canonical_execution")
            if isinstance(execution, Mapping):
                pending = execution.get("pending")
                blocked = execution.get("blocked")
                failed = execution.get("failed")
                if all(type(n) is int and 0 <= n <= 1_000_000
                       for n in (pending, blocked, failed)):
                    if failed == 0 and blocked == 0 and pending:
                        return "pending"
                    if failed == 0 and blocked and pending == 0:
                        return "blocked"
            if status == "pending" or reason == "delivery_unconfirmed":
                return "pending"
            if status == "blocked":
                return "blocked"
            if status == "partial":
                return "partial"
            sources = facts.get("sources")
            if isinstance(sources, Mapping):
                statuses = [
                    row.get("status") for row in sources.values()
                    if isinstance(row, Mapping)
                ]
                if statuses and all(item == "operation_busy" for item in statuses):
                    return "busy"
                if statuses and all(item == "cancelled" for item in statuses):
                    return "cancelled"
                if statuses and all(item == "pending" for item in statuses):
                    return "pending"
                if statuses and all(item == "blocked" for item in statuses):
                    return "blocked"
                if statuses and "partial" in statuses and not any(
                    item in ("error", "failed", "retryable_error") for item in statuses
                ):
                    return "partial"
        return "success" if value.ok is True else "failed"

    @staticmethod
    def _borrow_outcome(value: Any) -> str:
        """Only the Core manual-loan receipt is a typed business result.

        Legacy provider borrow workers return a (loan, loans) tuple; preserve
        their existing result contract. A BnF Core dict without verified
        confirmation is never an attested new loan.
        """
        if type(value) is not dict:
            return "success"
        status = value.get("status")
        if status == "confirmed" and value.get("provider_verified") is True:
            return "success"
        if status in {"submitted", "verification_unavailable"}:
            return "pending"
        if status in {"blocked", "unsupported"}:
            return "blocked"
        if status == "cancelled":
            return "cancelled"
        return "failed"

    @staticmethod
    def _local_transfer_outcome(value: Any) -> str:
        """Only the Core's closed independent local-copy receipt may succeed.

        A GTK callback can receive a normal return value on refused copy,
        stale source or failed physical verification. Those are *business*
        outcomes, not proof of successful transfer. Never trust arbitrary
        plugin truthiness or status subclasses.
        """
        if type(value) is not dict:
            return "failed"
        status = value.get("status")
        if type(status) is not str:
            return "failed"
        if status == "blocked":
            return "blocked"
        if status != "complete" or value.get("retry_borrow") is not False:
            return "failed"
        # Validate every source-controlled scalar before equality, not just
        # status: a str subclass can override __eq__ or __hash__.
        expected = (
            ("loan", "not_attempted"),
            ("delivery", "present"),
            ("transfer", "completed"),
            ("reason_code", "independent_local_copy_completed"),
        )
        for field, wanted in expected:
            candidate = value.get(field)
            if type(candidate) is not str or candidate != wanted:
                return "failed"
        copy = value.get("copy")
        return (
            "success"
            if type(copy) is str and copy in {"created", "already_present"}
            else "failed"
        )

    @staticmethod
    def _manual_acsm_outcome(value: Any) -> str:
        """Closed Core ACSM result, shared by manual and exact-loan imports.

        A staged ACSM with missing ADEPT profile is *pending*, not imported.
        A cancelled stale GTK action is not a successful artifact. No
        provider code, path, status subclass or raw exception crosses this
        task receipt boundary. 'unchanged' is an idempotent success, not
        an attestation of a new acquisition.
        """
        if type(value) is not dict:
            return "failed"
        status = value.get("status")
        if type(status) is not str:
            return "failed"
        if status in {"complete", "unchanged"}:
            # Core manual_acsm_import is the sole producer of these statuses.
            # Its positive receipt includes an exact durable acquisition ID,
            # an existing output path and, for a new artifact, its SHA-256.
            # A forged status-only dict is never evidence of a new file.
            acquisition_id = value.get("acquisition_id")
            path = value.get("path")
            if (type(acquisition_id) is not str
                    or not acquisition_id.startswith("manual-acsm:")
                    or len(acquisition_id) != 44
                    or any(ch not in "0123456789abcdef"
                           for ch in acquisition_id[12:])
                    or type(path) is not str or not 0 < len(path) <= 4096
                    or "\x00" in path):
                return "failed"
            if status == "unchanged":
                return "success"  # Idempotence, never a new acquisition.
            artifact_id = value.get("artifact_id")
            if (type(artifact_id) is not str
                    or not artifact_id.startswith("sha256:")
                    or len(artifact_id) != 71
                    or any(ch not in "0123456789abcdef"
                           for ch in artifact_id[7:])):
                return "failed"
            return "success"
        if status == "attention_required":
            return "pending"
        if status == "cancelled":
            return "cancelled"
        if status == "blocked":
            return "blocked"
        return "failed"

    @staticmethod
    def _lender_local_send_outcome(value: Any) -> str:
        """Only a confirmed A-owned loan plus completed B-local copy succeeds."""
        if type(value) is not dict or type(value.get("status")) is not str:
            return "failed"
        if value["status"] == "blocked":
            return "blocked"
        if value["status"] != "complete" or value.get("retry_borrow") is not False:
            return "failed"
        for field, wanted in (
            ("loan", "confirmed"),
            ("delivery", "present"),
            ("transfer", "completed"),
            ("reason_code", "independent_local_copy_completed"),
        ):
            candidate = value.get(field)
            if type(candidate) is not str or candidate != wanted:
                return "failed"
        return "success"

    @staticmethod
    def _borrow_verification_outcome(value: Any) -> str:
        """Read-only Core loan observation is not a new borrowing success."""
        if type(value) is not dict:
            return "failed"
        status = value.get("status")
        reason = value.get("reason_code")
        if (type(status) is not str or type(reason) is not str
                or value.get("retry_borrow") is not False
                or value.get("mutated") is not False):
            return "failed"
        if (status == "observed_current_loan"
                and reason == "borrow_verification_current_loan_observed"):
            return "success"
        if status == "unavailable":
            return "pending"
        if status == "blocked":
            return "blocked"
        return "failed"

    @staticmethod
    def _materialize_outcome(value: Any) -> str:
        """Physical local A→B copy succeeded only with a Core placement."""
        if type(value) is not dict:
            return "failed"
        status = value.get("status")
        target = value.get("target_identity_id")
        placement = value.get("placement_id")
        path = value.get("path")
        if (type(status) is not str or status not in ("complete", "unchanged")
                or type(target) is not str or not target
                or type(placement) is not str or not placement
                or type(path) is not str or not path or chr(0) in path):
            return "failed"
        return "success"

    @staticmethod
    def _loan_acquisition_outcome(value: Any) -> str:
        """Only Core's confirmed completed recovery is an acquired book."""
        if type(value) is not dict:
            return "failed"
        status = value.get("status")
        if type(status) is not str:
            return "failed"
        if status == "complete":
            return "success"
        if status == "attention_required":
            return "pending"
        if status in {"blocked", "refused"}:
            return "blocked"
        if status == "cancelled":
            return "cancelled"
        return "failed"

    @staticmethod
    def _explicit_acsm_stage_outcome(value: Any) -> str:
        """A staged ACSM is never an acquired book or fulfilled ADEPT loan."""
        if type(value) is not dict:
            return "failed"
        status = value.get("status")
        if type(status) is not str:
            return "failed"
        if status == "candidate_acsm":
            path = value.get("path")
            return (
                "success"
                if type(path) is str and 0 < len(path) <= 4096
                and chr(0) not in path and path.lower().endswith(".acsm")
                else "failed"
            )
        if status == "attention_required":
            return "pending"
        if status == "blocked":
            return "blocked"
        if status == "cancelled":
            return "cancelled"
        return "failed"

    @staticmethod
    def _lender_placement_check_outcome(value: Any) -> str:
        """A passive local observation neither borrows nor sends a book."""
        from cifereca.core.borrow_for_identity_placement_resolution import (
            LenderLocalPlacement,
        )
        if type(value) is not LenderLocalPlacement:
            return "failed"
        status, reason, loan = value.status, value.reason_code, value.loan
        if (type(status) is not str or type(reason) is not str
                or type(loan) is not str
                or value.executable is not False
                or value.retry_borrow is not False):
            return "failed"
        if (status == "present"
                and reason == "lender_exact_local_placement_present"
                and loan == "confirmed"):
            placement = value._placement_id
            return (
                "success"
                if type(placement) is str and 0 < len(placement) <= 1024
                and chr(0) not in placement else "failed"
            )
        if (status == "unavailable" and value._placement_id is None):
            if reason == "lender_delivery_unverified" and loan == "confirmed":
                return "pending"
            if (reason in {"lender_scope_unverified", "lender_loan_not_confirmed"}
                    and loan in {"unknown", "confirmed", "not_attempted"}):
                return "blocked"
        return "failed"

    @staticmethod
    def _loan_renew_outcome(value: Any) -> str:
        """A submitted RenewLoans POST is never a verified new due date.

        The Core returns only closed, no-private-data states. No renewal
        worker can mint a completed-loan success from HTTP acceptance.
        """
        if type(value) is not dict:
            return "failed"
        status = value.get("status")
        code = value.get("code")
        retry = value.get("retry")
        if (type(status) is not str or type(code) is not str
                or type(retry) is not str
                or type(value.get("provider_request_accepted")) is not bool
                or value.get("provider_verified") is not False):
            return "failed"
        if (status == "submitted"
                and code in ("renewal_provider_submitted",
                             "renewal_previous_submission")
                and value["provider_request_accepted"] is True
                and retry == "verification_only"):
            return "pending"
        if (status == "verification_unavailable"
                and code in ("renewal_previous_attempt_unverified",
                             "loan_scope_changed",
                             "renewal_provider_response_unverified",
                             "renewal_checkpoint_unavailable")
                and retry == "verification_only"):
            return "pending"
        if (status == "blocked"
                and code in ("renewal_confirmation_required",
                             "loan_scope_unverified", "loan_scope_changed",
                             "loan_permission_unverified", "loan_read_unavailable",
                             "renewal_checkpoint_unavailable", "loan_action_busy")
                and value["provider_request_accepted"] is False
                and retry == "explicit_only"):
            return "blocked"
        return "failed"

    def _business_outcome(self, value: Any, kind: str) -> str:
        """One exact Core receipt for worker metrics and GTK task feedback."""
        if kind == "reader-sync":
            from ..core.device_usb_sync_engine import UsbSyncResult
            if type(value) is not UsbSyncResult:
                return "failed"
            if value.status in ("cancelled", "blocked", "pending", "partial"):
                return value.status
            if value.status == "completed" and value.observed is True and not value.failed and not value.pending:
                return "success"
            return "failed"
        if kind == "borrow":
            return self._borrow_outcome(value)
        if kind == "transfer":
            return self._local_transfer_outcome(value)
        if kind == "acsm-import":
            return self._manual_acsm_outcome(value)
        if kind == "lender-local-send":
            return self._lender_local_send_outcome(value)
        if kind == "lender-placement-check":
            return self._lender_placement_check_outcome(value)
        if kind == "materialize":
            return self._materialize_outcome(value)
        if kind == "borrow-verification":
            return self._borrow_verification_outcome(value)
        if kind == "loan-acquisition":
            return self._loan_acquisition_outcome(value)
        if kind == "loan-renew":
            return self._loan_renew_outcome(value)
        if kind == "acsm-stage":
            return self._explicit_acsm_stage_outcome(value)
        return self._source_sync_outcome(value, kind)

    def is_active_automatic_opening(self) -> bool:
        """Passive GTK-side gate for periodic inventories during opening Sync.

        Only the Core-owned global automatic opening reservation is inspected.
        An in-flight result stays active until GTK delivery, avoiding a timer
        race at the end of the worker. This never grants inventory or download
        consent and does not release/replace the Core acquisition lock.
        """
        with self._lock:
            # Every generation remains active until its own GTK delivery.
            return any(key[:2] == ("opening-sync", "opening:global:shallow")
                       for key in self._coalesced)

    def close(self) -> None:
        with self._lock:
            self._coalesced.clear()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def submit(
        self,
        label: str,
        fn: Callable[[], Any] | Callable[[Callable[[str], None]], Any],
        ok: Callable[[Any], None] | None = None,
        err: Callable[[BaseException], None] | None = None,
        control: Gtk.Widget | None = None,
        success_toast: str | None = None,
        *,
        priority: TaskPriority = TaskPriority.NORMAL,
        kind: str = "task",
        with_progress: bool = False,
        coalesce_key: str | None = None,
        origin: str = "unspecified",
    ) -> None:
        """Enqueue work, optionally suppressing an identical automatic request.

        `coalesce_key` is strictly opt-in; a user-triggered request (control
        provided) is *never* coalesced. The first task retains its normal
        progress/completion callbacks, and all duplicate automatic requests
        observe that same running operation rather than opening new workers.
        No result is durably cached after GTK delivery.
        """
        # A late plugin/action must not disable a detached GTK control or
        # enqueue a worker after the window owner has been torn down.
        if getattr(self.window, "_closed", False):
            return
        # Reject invalid work before disabling a GTK widget, allocating a
        # coalescing reservation or publishing a phantom task-start callback.
        # PriorityExecutor enforces the same contract at its own boundary.
        if not callable(fn):
            raise TypeError("task must be callable")
        if type(priority) not in (int, TaskPriority):
            raise ValueError("invalid task priority")
        if coalesce_key is not None and type(kind) is not str:
            raise ValueError("invalid task coalescing kind")
        if coalesce_key is not None and (
            type(coalesce_key) is not str or not coalesce_key or len(coalesce_key) > 128
        ):
            raise ValueError("invalid task coalescing key")
        # The origin is an enumerated diagnostic, never an echoed plugin field.
        if type(origin) is not str or origin not in {
            "automatic", "navigation", "user", "maintenance", "unspecified",
        }:
            origin = "unspecified"
        # Never log raw display labels or plugin-supplied kind strings: these
        # may contain URLs, credentials or account details. Preserve the real
        # kind and label for routing and on-screen progress; sanitize TRACE only.
        trace_kind = kind if type(kind) is str and kind in {
            "task", "opening-sync", "deep-sync", "source-sync", "source-materialize",
            # Retired supplier-named tasks have no active CoreUI producer;
            # diagnostics use the neutral "other" bucket for legacy inputs.
            "sync-preflight", "maintenance", "library",
            "recent", "search", "plugin", "other",
            "maintenance-plan", "maintenance-tool",
            # Fixed, Core-owned task kinds already used by MainWindow.
            # Previously these appeared as "other" in local TRACE, making
            # timing samples impossible to attribute (#1741).
            "history", "bookings", "search-known", "detail-cache", "transfer", "materialize", "acsm-import", "lender-local-send", "lender-placement-check",
            "adept-inventory", "adept-return", "loan-acquisition", "acsm-stage", "borrow", "borrow-verification", "loan-renew",
            "obtain", "index", "provider-connect", "sync-audit", "source-consent",
            "identity", "maintenance-inspection", "reader-sync",
        } else "other"
        def classify_business(value: Any) -> str:
            """Use the exact TaskRunner classifier; isolated submit adapters
            may only retain generic, non-authorizing compatibility tasks.
            """
            # Old AST-only hosts inject a permissive generic dispatcher.
            # Without this class-owned typed renewal classifier, that mock
            # must never turn HTTP POST acceptance into a renewed loan.
            if (trace_kind == "loan-renew"
                    and not callable(getattr(self, "_loan_renew_outcome", None))):
                return "failed"
            classify = getattr(self, "_business_outcome", None)
            if callable(classify):
                return classify(value, trace_kind)
            # Historical AST-only submit() adapters are not Core authorities.
            # A missing typed classifier cannot mint a successful supplier
            # loan, physical copy, acquisition or DRM staging receipt.
            if trace_kind in {
                "borrow", "transfer", "acsm-import", "acsm-stage",
                "lender-local-send", "lender-placement-check",
                "borrow-verification", "loan-acquisition", "loan-renew", "materialize", "reader-sync",
            }:
                return "failed"
            legacy = getattr(self, "_source_sync_outcome", None)
            return legacy(value, trace_kind) if callable(legacy) else "failed"

        # An explicit user request must always run, including callers that
        # deliberately have no GTK control (keyboard actions, headless host).
        # A missing widget is not evidence that a task is automatic (#981).
        # Keep legacy unspecified-key coalescing for compatibility.
        admission = (
            (kind, coalesce_key)
            if coalesce_key is not None and control is None and origin != "user"
            else None
        )
        # The task's kind/key describe the mission, not the source lease.
        # A reconnected source (or replaced Core) must never inherit an old
        # in-flight result merely because the automatic key is identical.
        # These process-local coordinates are not emitted into TRACE.
        if admission is not None and kind in {"opening-sync", "source-sync"}:
            service = getattr(self.window, "service", None)
            epoch = getattr(service, "_source_context_epoch", 0)
            admission = (
                (*admission, id(service), epoch)
                if type(epoch) is int and epoch >= 0 else None
            )
        # A detached Gtk widget must be checked before activity counters or
        # reservations are allocated, as with the original TaskRunner. User
        # controls cannot be coalesced, so this cannot touch a skipped task.
        if control is not None and control.get_root() is not None:
            control.set_sensitive(False)
        with self._lock:
            active = getattr(self, "_coalesced", {})
            if admission is not None and admission in active:
                _TRACE_LOG.info(
                    "TRACE task coalesced active_id=%s kind=%s origin=%s",
                    active[admission], trace_kind, origin,
                )
                return
            task_id = next(self._task_ids)
            self._count += 1
            count = self._count
            if admission is not None:
                self._coalesced[admission] = task_id
        GLib.idle_add(self.window._task_started, task_id, count, label, int(priority), kind)

        def observe(
            phase: str, elapsed_seconds: float, outcome: str = "unknown",
        ) -> None:
            # Local closure also supports the historical isolated submit()
            # adapters that execute against a lightweight window host.
            observer = getattr(getattr(self.window, "ui_perf", None), "record_phase", None)
            if not callable(observer):
                return
            try:
                observer(
                    kind, phase, elapsed_seconds * 1000,
                    operation_id=task_id, trigger=origin, outcome=outcome,
                )
            except Exception:
                # Instrumentation must never affect a real task or GTK.
                pass

        def trace_failure(phase: str, exc: BaseException) -> None:
            # A worker's raw message can contain bearer tokens, cookies or
            # passwords. The task identity and exception *types* suffice to
            # correlate the local failure with its terminal task event.
            def name(value: object) -> str:
                label = type(value).__name__
                return label if (
                    type(label) is str and len(label) <= 64
                    and label.isidentifier()
                ) else "UnknownError"

            cause = getattr(exc, "__cause__", None)
            _TRACE_LOG.error(
                "TRACE task failure id=%s kind=%s origin=%s phase=%s "
                "exception=%s cause=%s",
                task_id, trace_kind, origin, phase,
                name(exc), name(cause) if cause is not None else "none",
            )

        queued_at = time.monotonic()
        _TRACE_LOG.info(
            "TRACE task submit id=%s priority=%s kind=%s origin=%s",
            task_id, int(priority), trace_kind, origin,
        )

        def report(progress_label: str) -> None:
            # Bind before enqueue: a fast worker can report before submit()
            # returns. The GTK adapter rechecks this exact task's lifetime
            # when dispatching, including callbacks retained by a producer.
            if not getattr(self.window, "_closed", False):
                GLib.idle_add(
                    self.window._task_progress_for_task, task_id, str(progress_label),
                )

        def traced_fn() -> Any:
            started = time.monotonic()
            observe("queue_wait", max(0, started - queued_at))
            _TRACE_LOG.info(
                "TRACE task begin id=%s priority=%s kind=%s queue_wait=%.3fs",
                task_id, int(priority), trace_kind, started - queued_at,
            )
            outcome_status = "success"
            try:
                value = fn(report) if with_progress else fn()
                business = classify_business(value)
                if business != "success":
                    outcome_status = "failed"
                    if trace_kind in {"borrow", "opening-sync", "deep-sync"}:
                        # Reuse the existing worker-scoped task_ref correlation
                        # for an attested non-success Core result. No synthetic
                        # exception, new event or cross-identity attribution.
                        from cifereca.diagnostic_task_context import diagnostic_task_business_result
                        diagnostic_task_business_result(value, operation_kind=trace_kind)
                return value
            except BaseException:
                outcome_status = "failed"
                raise
            finally:
                elapsed = max(0, time.monotonic() - started)
                observe("worker", elapsed, outcome_status)
                _TRACE_LOG.info(
                    "TRACE task end id=%s kind=%s dt=%.3fs",
                    task_id, trace_kind, elapsed,
                )

        try:
            future = self.pool.submit(priority, traced_fn)
        except RuntimeError as exc:
            trace_failure("enqueue", exc)
            # close() can shut down PriorityExecutor between the window's
            # initial closed gate and enqueue. Balance the activity counter
            # and release the control; never publish exception internals.
            with self._lock:
                self._count = max(0, self._count - 1)
                remaining = self._count
                if admission is not None and self._coalesced.get(admission) == task_id:
                    del self._coalesced[admission]
            _TRACE_LOG.info(
                "TRACE task outcome id=%s kind=%s origin=%s status=rejected",
                task_id, trace_kind, origin,
            )

            def rejected() -> bool:
                if getattr(self.window, "_closed", False):
                    return False
                if control is not None and control.get_root() is not None:
                    control.set_sensitive(True)
                self.window._task_finished(
                    task_id, remaining, label, False, "Traitement indisponible",
                )
                if getattr(self.window, "_closed", False):
                    return False
                failure = RuntimeError("Traitement indisponible")
                if err:
                    err(failure)
                elif origin != "automatic":
                    # The activity history already retains this exact failed
                    # task. Do not open a misleading modal over unrelated
                    # successful startup/provider synchronizations.
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
            if outcome[0] == "error":
                trace_failure("worker", outcome[1])
            from cifereca.plugin_api import Cancelled
            business_status = (
                ("cancelled" if isinstance(outcome[1], Cancelled) else "failed")
                if outcome[0] == "error"
                else classify_business(outcome[1])
            )
            # Isolated legacy submit() adapters do not carry the class's
            # helper methods. Those adapters cannot attest lock contention,
            # so they fail closed without changing their established result.
            lock_classifier = getattr(self, "_task_has_typed_lock_busy", None)
            typed_lock_busy = (
                outcome[0] == "error"
                and callable(lock_classifier)
                and lock_classifier(outcome[1])
            )
            classify_task = getattr(self, "_task_feedback_classification", None)
            task_classification = (
                classify_task(origin, trace_kind, control)
                if callable(classify_task) else
                "user_action" if origin == "user" or control is not None
                else "background_task"
            )
            # A completed worker may still be awaiting GTK delivery. Keep
            # its original result as the authoritative pending operation
            # until the UI consumes it; close() clears detached reservations.
            # Activity status stays success/failed for existing consumers,
            # while a closed business outcome distinguishes a busy lock from
            # a partial result or a genuine source-sync failure (#2888).
            detail = (
                f" business_status={business_status}"
                if trace_kind in {"source-sync", "borrow", "transfer", "acsm-import", "lender-local-send", "lender-placement-check", "borrow-verification", "materialize", "loan-acquisition", "acsm-stage", "loan-renew", "opening-sync", "deep-sync"} else ""
            )
            # Only Core's explicitly closed BnF reason vocabulary may enter
            # the generic task receipt. Ignore arbitrary provider detail.
            if trace_kind == "borrow" and outcome[0] == "ok" and type(outcome[1]) is dict:
                reason = outcome[1].get("reason_code")
                if type(reason) is str and reason in {
                    "borrow_confirmation_required", "borrow_site_check_required",
                    "borrow_scope_changed", "borrow_scope_unverified",
                    "borrow_capability_unavailable", "borrow_execution_unavailable",
                    "borrow_previous_attempt", "borrow_checkpoint_unavailable",
                    "borrow_cycle_not_ended",
                    "borrow_active_loan_observed", "borrow_not_submitted",
                    "borrow_result_unverified",
                }:
                    detail += f" reason_code={reason}"
            _TRACE_LOG.info(
                "TRACE task outcome id=%s kind=%s origin=%s status=%s%s",
                task_id, trace_kind, origin,
                "success" if business_status == "success" else "failed", detail,
            )

            gtk_pending_since = time.monotonic()

            def deliver() -> bool:
                # Some legacy isolation tests compile deliver() by itself.
                # Missing outer diagnostic closure must remain harmless.
                try:
                    busy_lock_attested = typed_lock_busy
                    feedback_classification = task_classification
                except NameError:
                    busy_lock_attested = False
                    feedback_classification = "background_task"
                # This callback is part of the real GTK/TaskRunner lifecycle:
                # it owns the pending coalescing reservation until delivery.
                # Its historical name also identifies the actual event held
                # in the GLib queue by deterministic race tests (#981).
                # Existing teardown tests compile deliver() alone, so a
                # missing enclosing observer must remain harmless.
                try:
                    phase_observer = observe
                    queued_completion_at = gtk_pending_since
                except NameError:
                    pass
                else:
                    phase_observer(
                        "gtk_delivery_wait",
                        max(0, time.monotonic() - queued_completion_at),
                        "success" if business_status == "success" else "failed",
                    )
                with self._lock:
                    self._count = max(0, self._count - 1)
                    remaining = self._count
                    # Only after GTK delivery can another identical
                    # automatic request be admitted. This avoids resubmitting
                    # a finished job while its UI result is still queued.
                    for key, owner_id in tuple(getattr(self, "_coalesced", {}).items()):
                        if owner_id == task_id:
                            del self._coalesced[key]
                if getattr(self.window, '_closed', False):
                    return False
                # Existing lifecycle signal, not another scheduler/timer.
                # The optional Core adapter only submits a nonblocking hint.
                tick = getattr(self.window, "_notice_work_tick", None)
                if callable(tick):
                    tick()
                if control is not None and control.get_root() is not None:
                    control.set_sensitive(True)
                kind_outcome, payload = outcome
                if busy_lock_attested:
                    from cifereca.gui.ui_dialog_observation import observe_background_busy_task
                    observe_background_busy_task(
                        self.window, task_ref=task_id,
                        classification=feedback_classification,
                    )
                feedback_error = None if business_status == "success" else (
                    "Une autre opération est déjà en cours"
                    if busy_lock_attested else
                    "Emprunt non confirmé — vérification nécessaire"
                    if trace_kind == "borrow" and business_status == "pending"
                    else "Emprunt bloqué — aucune confirmation"
                    if trace_kind == "borrow" and business_status == "blocked"
                    else "Emprunt annulé"
                    if trace_kind == "borrow" and business_status == "cancelled"
                    else "Emprunt non confirmé"
                    if trace_kind == "borrow" and business_status != "success"
                    else "Copie locale bloquée"
                    if trace_kind == "transfer" and business_status == "blocked"
                    else "Copie locale non confirmée"
                    if trace_kind == "transfer"
                    else "Import ACSM en attente — configuration DRM nécessaire"
                    if trace_kind == "acsm-import" and business_status == "pending"
                    else "Import ACSM annulé"
                    if trace_kind == "acsm-import" and business_status == "cancelled"
                    else "Import ACSM bloqué"
                    if trace_kind == "acsm-import" and business_status == "blocked"
                    else "Import ACSM non confirmé"
                    if trace_kind == "acsm-import"
                    else "Copie du prêteur bloquée"
                    if trace_kind == "lender-local-send" and business_status == "blocked"
                    else "Copie du prêteur non confirmée"
                    if trace_kind == "lender-local-send"
                    else "Placement du prêteur en attente de livraison"
                    if trace_kind == "lender-placement-check" and business_status == "pending"
                    else "Vérification du placement bloquée"
                    if trace_kind == "lender-placement-check" and business_status == "blocked"
                    else "Placement du prêteur non confirmé"
                    if trace_kind == "lender-placement-check"
                    else "Envoi du fichier local non confirmé"
                    if trace_kind == "materialize"
                    else "Récupération du livre en attente"
                    if trace_kind == "loan-acquisition" and business_status == "pending"
                    else "Récupération du livre non confirmée"
                    if trace_kind == "loan-acquisition"
                    else "Jeton ACSM en attente"
                    if trace_kind == "acsm-stage" and business_status == "pending"
                    else "Jeton ACSM non confirmé"
                    if trace_kind == "acsm-stage"
                    else "Prêt non encore vérifié — aucune nouvelle demande envoyée"
                    if trace_kind == "borrow-verification" and business_status == "pending"
                    else "Vérification du prêt bloquée"
                    if trace_kind == "borrow-verification" and business_status == "blocked"
                    else "Vérification du prêt non confirmée"
                    if trace_kind == "borrow-verification"
                    else "Prolongation transmise — échéance non vérifiée"
                    if trace_kind == "loan-renew" and business_status == "pending"
                    else "Demande de prolongation bloquée"
                    if trace_kind == "loan-renew" and business_status == "blocked"
                    else "Prolongation non confirmée"
                    if trace_kind == "loan-renew"
                    else "Synchronisation déjà en cours" if business_status == "busy"
                    else "Synchronisation en attente de livraison" if business_status == "pending"
                    else "Synchronisation bloquée" if business_status == "blocked"
                    else "Synchronisation annulée" if business_status == "cancelled"
                    else "Synchronisation partielle" if business_status == "partial"
                    else "Échec de synchronisation" if kind_outcome == "ok"
                    else payload.user_message if isinstance(payload, UserFacingError)
                    else "Traitement indisponible"
                )
                # Preserve main's explicit pending-borrow instruction,
                # independent of the background lock-contention feedback.
                if (business_status == "pending" and not busy_lock_attested
                        and trace_kind == "borrow"
                        and kind_outcome == "ok" and type(payload) is dict
                        and payload.get("reason_code") == "borrow_result_unverified"):
                    feedback_error = (
                        "Demande d’emprunt envoyée ; confirmation indisponible. "
                        "Ne réempruntez pas : actualisez vos prêts ultérieurement."
                    )
                finish_args = (
                    task_id, remaining, label,
                    business_status == "success", feedback_error,
                )
                if (business_status in {"pending", "partial", "blocked", "busy", "cancelled"}
                        and trace_kind in {"source-sync", "borrow", "transfer", "acsm-import", "lender-local-send", "lender-placement-check", "borrow-verification", "materialize", "loan-acquisition", "acsm-stage", "loan-renew", "opening-sync", "deep-sync"}):
                    self.window._task_finished(*finish_args, business_status)
                else:
                    self.window._task_finished(*finish_args)
                if kind_outcome == "ok":
                    if ok:
                        try:
                            ok(payload)
                        except Exception as callback_error:
                            trace_failure("gtk_ok_callback", callback_error)
                            if getattr(self.window, "_closed", False):
                                return False
                            # Worker success is not a successful UI delivery.
                            # Reclassify the same receipt in the activity
                            # history; do not pretend the worker itself failed
                            # or assign this to another provider/task.
                            reporter = getattr(self.window, "_activity_result", None)
                            if callable(reporter):
                                reporter(
                                    "Traitement indisponible", success=False,
                                    task_id=task_id,
                                )
                            if origin != "automatic":
                                self.present_error(callback_error)
                            return False
                    if getattr(self.window, "_closed", False):
                        return False
                    # A success callback may close the window itself.
                    # Never update detached GTK feedback after that callback.
                    if (business_status == "success" and success_toast
                            and not getattr(self.window, "_closed", False)):
                        reporter = getattr(self.window, "_activity_result", None)
                        if callable(reporter):
                            reporter(success_toast, success=True, task_id=task_id)
                        else:
                            self.window.toast(success_toast)
                else:
                    if getattr(self.window, "_closed", False):
                        return False
                    # Carry the exact TaskRunner identity into *this* GTK
                    # completion only; no context survives a later callback.
                    # A background lock contention is nonmodal even if an
                    # existing provider-specific err() calls show_error().
                    from cifereca.gui.ui_dialog_observation import task_dialog_context
                    with task_dialog_context(
                        self.window, task_ref=task_id,
                        classification=feedback_classification,
                        reason_code="lock_busy" if busy_lock_attested else "unknown",
                        suppress_busy_modal=(
                            busy_lock_attested
                            and origin in {"automatic", "maintenance", "navigation"}
                            and control is None
                        ),
                    ):
                        if err:
                            try:
                                err(payload)
                            except Exception as callback_error:
                                trace_failure("gtk_error_callback", callback_error)
                                if getattr(self.window, "_closed", False):
                                    return False
                                if origin != "automatic":
                                    # A *new* failure in the error callback is
                                    # not the worker's attested busy lock.
                                    # Preserve its actionable dialog, scoped
                                    # to the same task, with no suppression.
                                    with task_dialog_context(
                                        self.window, task_ref=task_id,
                                        classification=feedback_classification,
                                        reason_code="unknown",
                                        suppress_busy_modal=False,
                                    ):
                                        self.present_error(callback_error)
                        elif origin != "automatic":
                            # The caller remains responsible for marking user
                            # intent. Background contention keeps its task
                            # receipt and nonblocking status instead of a modal.
                            self.present_error(payload)
                return False

            # A confirmed, cancellable source materialization must deliver
            # its final GTK receipt even under sustained repaint/progress
            # traffic. This priority is bound to the generic task contract,
            # never the provider ID; unrelated task kinds retain idle order.
            # Expedited GTK materialization delivery belongs solely to the
            # supplier-neutral Core kind; unknown/retired names stay generic.
            normal_priority = (
                getattr(GLib, "PRIORITY_DEFAULT", None)
                if trace_kind == "source-materialize" else None
            )
            if normal_priority is None:
                # Headless adapters implementing the legacy GLib seam keep
                # their existing scheduling contract.
                GLib.idle_add(deliver)
            else:
                GLib.idle_add(deliver, priority=normal_priority)

        future.add_done_callback(done)

    def present_error(self, payload: BaseException) -> None:
        if isinstance(payload, AttentionRequired):
            _TRACE_LOG.warning(
                "TRACE attention-required status=attention detail_present=%s",
                bool(payload.technical_detail),
            )
            self.window.show_attention(payload.user_message, code=payload.code)
        elif isinstance(payload, UserFacingError):
            _TRACE_LOG.warning(
                "TRACE user-facing-error status=error detail_present=%s",
                bool(payload.technical_detail),
            )
            self.window.show_error(payload.user_message)
        else:
            # Untyped exceptions often contain URLs, file paths, headers or
            # raw provider responses. Their text must never reach GTK feedback.
            self.window.show_error("Traitement indisponible")
