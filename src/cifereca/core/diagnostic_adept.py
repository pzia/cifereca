"""Core publication schema for the installed Kobo-ADEPT runtime's facts."""
from __future__ import annotations

import json
import re

PLUGIN_ID = "runtime.kobo-adept"
PACKAGE_ID = "org.cifereca.runtime.kobo-adept"
PREFIX = "TRACE adept runtime "
OPERATIONS = frozenset({"download request", "acsm return", "profile list", "profile show",
                        "loans list", "loans expired", "contract", "capabilities", "status",
                        "other", "unknown"})
ERROR_CODES = frozenset({
    "adept_error", "adept_license_already_fulfilled_by_another_user",
    "profile_missing", "invalid_profile", "kobo_adept_failed", "kobo_adept_missing",
    "kobo_adept_timeout", "kobo_adept_launch_error", "kobo_adept_invalid_json",
    "kobo_adept_invalid_utf8", "kobo_adept_invalid_response", "kobo_adept_output_too_large",
})
_METRICS = {"duration_ms": (0, 86_400_000), "stdout_bytes": (0, 67_108_864),
            "stderr_bytes": (0, 67_108_864), "returncode": (-255, 255)}


def public_fields(value: object) -> dict:
    if type(value) is not dict or set(value) - {
        "schema_version", "plugin_id", "operation_kind", "operation", "phase",
        "status", "code", "retryable", "task_ref", *_METRICS,
    }:
        return {}
    for key, expected in (("plugin_id", PLUGIN_ID), ("operation_kind", "kobo_adept"),
                          ("phase", "completed")):
        if type(value.get(key)) is not str or value[key] != expected:
            return {}
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return {}
    if type(value.get("operation")) is not str or value["operation"] not in OPERATIONS:
        return {}
    status, code = value.get("status"), value.get("code")
    if type(status) is not str or type(code) is not str:
        return {}
    if not ((status == "success" and code == "completed") or
            (status == "failed" and code in ERROR_CODES | {"code_non_disponible"})):
        return {}
    if "retryable" in value and (code not in ERROR_CODES or type(value["retryable"]) is not bool):
        return {}
    for key, (low, high) in _METRICS.items():
        if key in value and (type(value[key]) is not int or not low <= value[key] <= high):
            return {}
    if status == "success" and "returncode" in value and value["returncode"] != 0:
        return {}
    if "task_ref" in value and (type(value["task_ref"]) is not str or
                                re.fullmatch(r"[0-9a-f]{12}", value["task_ref"]) is None):
        return {}
    return dict(value)


def from_runtime(detail: object, status: object) -> dict:
    """Called only after the Core manager attests plugin AND package identity.

    The callback message, summaries, exception text and process streams are
    deliberately never read. Unknown codes do not attest retryability.
    """
    if type(detail) is not dict or type(status) is not str or status not in {"ok", "error"}:
        return {}
    operation = detail.get("operation")
    if type(operation) is not str or operation not in OPERATIONS:
        operation = "unknown"
    code = detail.get("error_code")
    code = code if type(code) is str and code in ERROR_CODES else "code_non_disponible"
    fields = dict(schema_version=1, plugin_id=PLUGIN_ID, operation_kind="kobo_adept",
                  operation=operation, phase="completed", status="failed" if status == "error" else "success",
                  code=code if status == "error" else "completed")
    if status == "error" and code in ERROR_CODES and type(detail.get("retryable")) is bool:
        fields["retryable"] = detail["retryable"]
    for key, (low, high) in _METRICS.items():
        value = detail.get(key)
        if type(value) is int and low <= value <= high:
            fields[key] = value
    return public_fields(fields)


def log_message(fields: dict) -> str:
    return PREFIX + json.dumps(fields, sort_keys=True, separators=(",", ":"))


def public_log_fields(body: object) -> dict:
    if type(body) is not str or len(body) > 2048 or not body.startswith(PREFIX):
        return {}
    try:
        fields = public_fields(json.loads(body[len(PREFIX):]))
    except (ValueError, TypeError, RecursionError):
        return {}
    return fields if fields and log_message(fields) == body else {}


def public_message(fields: dict) -> str:
    return "kobo_adept" + "".join(f" {key}={value}" for key, value in sorted(fields.items()))
