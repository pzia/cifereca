"""Core source inventory decisions: one closed v1 schema for both channels."""
from __future__ import annotations

import json
import re

from .public_provider_diagnostics import canonical_public_provider_id
from .source_sync_timing import TIMING_FIELDS

PREFIX = "TRACE source sync "
_OUTCOMES = {
    ("start", "running"): "source_sync_started",
    ("completed", "success"): "source_sync_complete",
    ("completed", "partial"): "source_sync_partial",
    ("completed", "skipped"): "source_disabled",
    ("completed", "failed"): frozenset({"source_sync_failed", "operation_busy"}),
    ("completed", "cancelled"): "source_sync_cancelled",
}


def public_fields(value: object) -> dict:
    if type(value) is not dict or set(value) - {
        "schema_version", "provider_id", "operation_kind", "phase", "status",
        "code", "duration_ms", "task_ref", "loans", "library",
    } - TIMING_FIELDS:
        return {}
    if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or canonical_public_provider_id(value.get("provider_id")) is None
            or type(value.get("operation_kind")) is not str
            or value["operation_kind"] != "source_sync"):
        return {}
    if any(type(value.get(k)) is not str for k in ("phase", "status", "code")):
        return {}
    expected = _OUTCOMES.get((value["phase"], value["status"]))
    if expected is None or (value["code"] not in expected if isinstance(expected, frozenset)
                            else value["code"] != expected):
        return {}
    if any(key in value for key in TIMING_FIELDS) and value["phase"] != "completed":
        return {}
    for key in TIMING_FIELDS:
        if key in value and (type(value[key]) is not int or not 0 <= value[key] <= 86_400_000):
            return {}
    for key, limit in (("duration_ms", 86_400_000), ("loans", 1_000_000), ("library", 1_000_000)):
        if key in value and (type(value[key]) is not int or not 0 <= value[key] <= limit):
            return {}
    if "task_ref" in value and (type(value["task_ref"]) is not str or
                                re.fullmatch(r"[0-9a-f]{12}", value["task_ref"]) is None):
        return {}
    return dict(value)


def log_message(fields: object) -> str | None:
    safe = public_fields(fields)
    return PREFIX + json.dumps(safe, sort_keys=True, separators=(",", ":")) if safe else None


def public_log_fields(body: object) -> dict:
    if type(body) is not str or len(body) > 2048 or not body.startswith(PREFIX):
        return {}
    try:
        fields = public_fields(json.loads(body[len(PREFIX):]))
    except (ValueError, TypeError, RecursionError):
        return {}
    return fields if fields and log_message(fields) == body else {}


def public_message(fields: dict) -> str:
    return "source_sync" + "".join(f" {k}={v}" for k, v in sorted(fields.items()))
