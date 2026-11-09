"""Retired Kobo 0.7 startup schedule compatibility keys (#4858 D4).

Legacy preferences remain persisted for inspection and rollback history but
never authorize a local GET or advance the retired retry clock. Startup
materialization is owned only by generic canonical source Sync.
"""
from __future__ import annotations

from typing import Any

_PREFIX = "sync.source.materialization.on_start."
_RETRY_PREFIX = "sync.source.materialization.retry_minutes."


def policy_key(source_id: str) -> str:
    """Read-only compatibility key for the retired source-specific preference."""
    return _PREFIX + source_id


def retry_interval_key(source_id: str) -> str:
    """Read-only compatibility key; no W2 retry scheduling remains."""
    return _RETRY_PREFIX + source_id


def can_enable_kobo_on_start(service: Any, account: Any) -> bool:
    """The old W2 on_start opt-in cannot be enabled after D4 retirement."""
    return False


def kobo_on_start_sessions(service: Any) -> tuple[Any, ...]:
    """No legacy startup session is eligible for W2 execution."""
    return ()


def on_start_due(service: Any, account: Any) -> bool:
    """The retired W2 retry clock never authorizes a start."""
    return False


def mark_on_start_attempt(service: Any, account: Any) -> bool:
    """Do not mutate historical W2 attempt clocks or inferred consent."""
    return False
