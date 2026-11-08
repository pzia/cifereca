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
        self._coalesced: dict[tuple[str, str], int] = {}

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

    def is_active_automatic_opening(self) -> bool:
        """Passive GTK-side gate for periodic inventories during opening Sync.

        Only the Core-owned global automatic opening reservation is inspected.
        An in-flight result stays active until GTK delivery, avoiding a timer
        race at the end of the worker. This never grants inventory or download
        consent and does not release/replace the Core acquisition lock.
        """
        with self._lock:
            return ("opening-sync", "opening:global:shallow") in self._coalesced

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
            "sync-preflight", "kobo-acquisition", "maintenance", "library",
            "recent", "search", "plugin", "other",
            "maintenance-plan", "maintenance-tool",
            # Fixed, Core-owned task kinds already used by MainWindow.
            # Previously these appeared as "other" in local TRACE, making
            # timing samples impossible to attribute (#1741).
            "history", "bookings", "search-known", "detail-cache",
            "adept-inventory", "adept-return", "loan-acquisition", "borrow",
            "obtain", "index", "provider-connect", "sync-audit", "source-consent",
            "identity", "maintenance-inspection",
        } else "other"
        # An explicit user request must always run, including callers that
        # deliberately have no GTK control (keyboard actions, headless host).
        # A missing widget is not evidence that a task is automatic (#981).
        # Keep legacy unspecified-key coalescing for compatibility.
        admission = (
            (kind, coalesce_key)
            if coalesce_key is not None and control is None and origin != "user"
            else None
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
                business = (
                    self._borrow_outcome(value) if trace_kind == "borrow"
                    else self._source_sync_outcome(value, trace_kind)
                )
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
                else (self._borrow_outcome(outcome[1]) if trace_kind == "borrow"
                      else self._source_sync_outcome(outcome[1], trace_kind))
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
                if trace_kind in {"source-sync", "borrow", "opening-sync", "deep-sync"} else ""
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
                        and trace_kind in {"source-sync", "borrow", "opening-sync", "deep-sync"}):
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

            # Terminal outcomes are more important than repaint/progress
            # idle traffic. A default-idle (priority 200) can starve while
            # GTK has ready events at default priority (0), leaving a
            # cancelled Kobo task visible as "Annulation demandée" long
            # after its worker returned. Preserve idle_add's thread-safe
            # dispatch while elevating only this final receipt.
            # Keep other providers' UI ordering untouched: only the
            # explicitly confirmed, cancellable Kobo acquisition benefits
            # from this expedited terminal receipt.
            normal_priority = (
                getattr(GLib, "PRIORITY_DEFAULT", None)
                if trace_kind == "kobo-acquisition" else None
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
