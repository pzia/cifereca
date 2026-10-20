"""Strict public projection of the Core Kobo backup opening decision (#2380).

One closed, bounded schema for SQLite Diagnostics and the fixed TRACE line.
These are two *observations*, not a correlation or shared task reference.
"""
from __future__ import annotations

import re

_STATUSES = frozenset({
    "disabled", "no_session", "capability_unavailable",
    "source_disabled", "manual_only", "started", "fresh_cache",
    "partial", "error",
})
_TRACE = re.compile(
    r"TRACE kobo backup decision status=([a-z_]{1,32})"
    r" enabled=(True|False) connected=([0-9]{1,6})"
    r" eligible=([0-9]{1,6}) interval=([0-9]{1,6})"
)


def _project(status, enabled, connected, eligible, interval) -> dict:
    if (type(status) is not str or status not in _STATUSES
            or type(enabled) is not bool
            or any(type(value) is not int or not 0 <= value <= 100_000
                   for value in (connected, eligible))
            or type(interval) is not int or not 1 <= interval <= 100_000
            or eligible > connected):
        return {}
    return {
        "operation_kind": "kobo_backup",
        "phase": "decision",
        "status": status,
        "enabled": enabled,
        "connected": connected,
        "eligible": eligible,
        "interval_minutes": interval,
    }


def public_kobo_backup_decision(detail: object) -> dict:
    """Ignore all unapproved fields (IDs, paths, errors, plugin payloads)."""
    if type(detail) is not dict:
        return {}
    return _project(
        detail.get("status"), detail.get("persisted_enabled"),
        detail.get("connected_kobo"), detail.get("eligible_kobo"),
        detail.get("interval_minutes"),
    )


def public_kobo_backup_log(body: object) -> dict:
    """A full exact Core-generated line, never a substring or loose key scan."""
    if type(body) is not str or len(body) > 200:
        return {}
    match = _TRACE.fullmatch(body)
    if match is None:
        return {}
    return _project(
        match[1], match[2] == "True",
        int(match[3]), int(match[4]), int(match[5]),
    )


def public_kobo_backup_message(fields: object) -> str | None:
    if type(fields) is not dict or fields.get("operation_kind") != "kobo_backup":
        return None
    if _project(
        fields.get("status"), fields.get("enabled"),
        fields.get("connected"), fields.get("eligible"),
        fields.get("interval_minutes"),
    ) != fields:
        return None
    return (
        "kobo backup decision"
        f" status={fields['status']}"
        f" enabled={fields['enabled']}"
        f" connected={fields['connected']}"
        f" eligible={fields['eligible']}"
        f" interval={fields['interval_minutes']}"
    )
