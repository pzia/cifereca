"""Read-only allowlisted projections of historical Kobo backup diagnostics.

This module never invokes a provider, opens SQLite, reads a file or executes
the retired W2 engine. Its only purpose is to redact untrusted historical
diagnostics for CLI and shareable support reports. No A/B rights are inferred.
"""
from __future__ import annotations

from typing import Any


def _safe_kobo_opening_decision(raw: Any) -> dict[str, Any] | None:
    """Strictly filter the durable opening decision before UI/CLI export.

    The database value may have been altered by an old installation or a
    plugin. Do not forward arbitrary keys, messages, identifiers or URLs.
    """
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        return None
    allowed = {
        "disabled", "no_session", "capability_unavailable", "source_disabled",
        "fresh_cache", "started", "partial", "error", "manual_only",
    }
    status = raw.get("status")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": status if type(status) is str and status in allowed else "unknown",
        "persisted_enabled": raw.get("persisted_enabled") is True,
    }
    for key in ("connected_kobo", "eligible_kobo", "scheduled_kobo"):
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 1_000_000:
            result[key] = value
    checked = raw.get("checked_at")
    if type(checked) is int and 0 <= checked <= 4_102_444_800:
        result["checked_at"] = checked
    return result
