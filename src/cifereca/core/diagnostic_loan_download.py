"""Closed Core-owned terminal observations for explicit digital-loan recovery.

Only the Core producer attests these events. Optional observed business IDs,
worker reference and causes are preserved without credentials (#3632).
Automatic sync is NOT an explicit attempt.
"""
from __future__ import annotations

import json
import re

from .public_provider_diagnostics import canonical_public_provider_id

PREFIX = "EVENT loan download "
STATUS = frozenset({"success", "failed", "attention_required", "retryable_error", "refused", "cancelled"})
CODES = frozenset({
    "completed", "reacquisition_failed", "provider_unavailable",
    "provider_disconnected", "download_unsupported", "not_digital",
    "adept_identity_unproven", "adept_loan_identity_unproven",
    "adept_loan_identity_conflict", "acquisition_busy", "adept_error",
    "pnb_redirect_refused", "provider_failure", "artifact_invalid",
    "operation_cancelled", "unclassified_error",
})
PHASES = frozenset({"admission", "terminal"})


def public_fields(value: object) -> dict[str, object]:
    required = {
        "schema_version", "provider_id", "operation_kind", "status",
        "code", "phase", "duration_ms",
    }
    if type(value) is not dict or not required <= set(value) or set(value) - required - {"task_ref", "loan_id", "cause"}:
        return {}
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or canonical_public_provider_id(value["provider_id"]) is None
            or type(value["operation_kind"]) is not str or value["operation_kind"] != "download_loan"
            or type(value["status"]) is not str or value["status"] not in STATUS
            or type(value["code"]) is not str or value["code"] not in CODES
            or type(value["phase"]) is not str or value["phase"] not in PHASES
            or type(value["duration_ms"]) is not int
            or not 0 <= value["duration_ms"] <= 86_400_000):
        return {}
    if value["status"] == "success" and value["code"] != "completed":
        return {}
    if value["code"] == "completed" and value["status"] != "success":
        return {}
    from .diagnostic_acquisition_failure import protect_text
    fields = dict(value)
    if "task_ref" in fields and (type(fields["task_ref"]) is not str
                                 or re.fullmatch(r"[0-9a-f]{12}", fields["task_ref"]) is None):
        return {}
    if "loan_id" in fields:
        if type(fields["loan_id"]) is not str or not 0 < len(fields["loan_id"]) <= 4096:
            return {}
        fields["loan_id"] = protect_text(fields["loan_id"])
    if "cause" in fields:
        cause = fields["cause"]
        if (type(cause) is not dict or not {"type", "message"} <= set(cause)
                or set(cause) - {"type", "message", "traceback", "http_status"}
                or any(type(cause.get(k)) is not str for k in ("type", "message"))
                or not 0 < len(cause["type"]) <= 160 or len(cause["message"]) > 8192
                or ("traceback" in cause and (type(cause["traceback"]) is not str or len(cause["traceback"]) > 8192))
                or ("http_status" in cause and (type(cause["http_status"]) is not int or not 100 <= cause["http_status"] <= 599))):
            return {}
        fields["cause"] = {key: protect_text(item) if type(item) is str else item for key, item in cause.items()}
    return fields


def log_message(fields: object) -> str | None:
    safe = public_fields(fields)
    return PREFIX + json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=True) if safe else None


def public_log_fields(line: object) -> dict[str, object]:
    if type(line) is not str or len(line) > 262144 or not line.startswith(PREFIX):
        return {}
    try:
        fields = public_fields(json.loads(line[len(PREFIX):]))
    except (TypeError, ValueError, RecursionError):
        return {}
    return fields if fields and log_message(fields) == line else {}
