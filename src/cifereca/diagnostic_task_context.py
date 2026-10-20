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
            try:
                _LOG.info('TRACE task correlation task_ref=%s status=%s', scope.reference, outcome)
            except Exception:
                pass
