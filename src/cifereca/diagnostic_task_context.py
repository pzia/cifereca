"""Lazy, worker-local diagnostic correlation; never an account or task label."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import secrets

_LOG = logging.getLogger('cifereca')


@dataclass
class _Scope:
    reference: str | None = None
    business_failed: bool = False
    business_cancelled: bool = False


_CURRENT: ContextVar[_Scope | None] = ContextVar('diagnostic_task', default=None)


def diagnostic_task_reference() -> str | None:
    """Return the actual worker scope, allocating only for an observed event."""
    scope = _CURRENT.get()
    if scope is None:
        return None
    if scope.reference is None:
        try:
            scope.reference = secrets.token_hex(6)
        except Exception:
            return None  # optional diagnostics cannot break a provider action
    return scope.reference


def diagnostic_task_business_result(
    result: object, *, operation_kind: str | None = None,
) -> None:
    """Mark an attested Core business refusal without raising an exception.

    The Future still returns the original result, which consumers require to
    refresh UI/broker state. A business refusal must not emit a worker
    correlation `status=success` merely because no Python error was thrown.
    No task reference is allocated unless a real event already requested it.
    """
    scope = _CURRENT.get()
    if scope is None:
        return
    from .plugin_api import SyncResult
    if isinstance(result, SyncResult) and result.ok is False:
        from collections.abc import Mapping
        facts = result.facts
        cancelled = False
        if isinstance(facts, Mapping):
            status, reason = facts.get("status"), facts.get("reason")
            cancelled = (
                (type(status) is str and status == "cancelled")
                or (type(reason) is str
                    and reason in {"cancelled", "source_sync_cancelled"})
            )
            sources = facts.get("sources")
            if (not cancelled and isinstance(sources, Mapping) and sources
                    and all(isinstance(row, Mapping)
                            and type(row.get("status")) is str
                            and row.get("status") == "cancelled"
                            for row in sources.values())):
                cancelled = True
        if cancelled:
            scope.business_cancelled = True
        else:
            scope.business_failed = True
    elif (operation_kind == "borrow" and type(result) is dict
          and type(result.get("status")) is str
          and result.get("status") in {
              "blocked", "unsupported", "failed", "cancelled",
              "submitted", "verification_unavailable",
          }
          and type(result.get("reason_code")) is str
          and type(result.get("provider_verified")) is bool):
        # Only the explicit Core manual-borrow result contract is accepted.
        # Arbitrary dicts returned by unrelated tasks are not business proof.
        if result["status"] == "cancelled":
            scope.business_cancelled = True
        else:
            scope.business_failed = True
    elif operation_kind in {"opening-sync", "deep-sync"} and type(result) is dict:
        rows = result.get("identities")
        if type(rows) is list and rows:
            statuses = [
                row.get("status") if type(row) is dict else None
                for row in rows[:65]
            ]
            if len(rows) > 64 or any(
                status not in {"success", "partial", "failed", "cancelled"}
                for status in statuses
            ) or "failed" in statuses:
                scope.business_failed = True
            elif "cancelled" in statuses:
                scope.business_cancelled = True
            elif "partial" in statuses:
                scope.business_failed = True


def numilog_task_reference() -> str | None:
    """Compatibility name for existing Numilog producers."""
    return diagnostic_task_reference()


@contextmanager
def diagnostic_task_scope():
    scope = _Scope()
    token = _CURRENT.set(scope)
    outcome = 'success'
    try:
        yield
    except BaseException as exc:
        from .plugin_api import Cancelled
        outcome = 'cancelled' if isinstance(exc, Cancelled) else 'failed'
        raise
    finally:
        # Reset before Future callbacks/reuse of this worker. An optional
        # broken log handler must not change the result of an operation.
        _CURRENT.reset(token)
        if scope.reference is not None:
            if outcome == 'success':
                if scope.business_cancelled:
                    outcome = 'cancelled'
                elif scope.business_failed:
                    outcome = 'failed'
            try:
                level = (logging.ERROR if outcome == 'failed' else
                         logging.WARNING if outcome == 'cancelled' else logging.INFO)
                _LOG.log(level, 'TRACE task correlation task_ref=%s status=%s', scope.reference, outcome)
            except Exception:
                pass
