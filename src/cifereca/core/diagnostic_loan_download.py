"""Closed Core-owned terminal observations for explicit digital-loan recovery.

No plugin/provider error, loan ID, account, title, path, ACSM URL or private
process correlation is admitted. Automatic sync is NOT an explicit attempt.
"""
from __future__ import annotations

import json

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
    if type(value) is not dict or set(value) != {
        "schema_version", "provider_id", "operation_kind", "status",
        "code", "phase", "duration_ms",
    }:
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
    return dict(value)


def log_message(fields: object) -> str | None:
    safe = public_fields(fields)
    return PREFIX + json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=True) if safe else None


def public_log_fields(line: object) -> dict[str, object]:
    if type(line) is not str or len(line) > 600 or not line.startswith(PREFIX):
        return {}
    try:
        fields = public_fields(json.loads(line[len(PREFIX):]))
    except (TypeError, ValueError, RecursionError):
        return {}
    return fields if fields and log_message(fields) == line else {}
