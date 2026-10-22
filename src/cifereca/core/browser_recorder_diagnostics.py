"""Closed, publication-safe facts for explicit Browser Recorder operations.

Both the Core Diagnostics collector and its public feed validate this same
vocabulary. A generic plugin diagnostic record cannot attest provenance.
"""
from __future__ import annotations

PLUGIN_ID = "ui.page.browser-recorder"
OPERATIONS = {
    "python_dependencies_install": "browser recorder python dependencies",
    "chromium_probe": "browser recorder chromium verification",
    "chromium_install": "browser recorder chromium installation",
    "http_capture": "browser recorder HTTP capture",
    "http_review": "browser recorder HTTP review",
    "zip_prepare": "browser recorder ZIP preparation",
    "zip_export": "browser recorder ZIP export",
    "private_zip_export": "browser recorder private ZIP export",
    "har_record": "browser recorder HAR recording",
    "har_replay": "browser recorder HAR replay",
}
# Only explicit runtime user actions may be attested through the scoped plugin
# context. Plugin code is never authorized to impersonate Core provisioning.
RUNTIME_CODES_BY_OPERATION = {
    "http_capture": frozenset({
        "capture_failed", "capture_interrupted", "capture_limit", "playwright_missing",
    }),
    "http_review": frozenset({"review_failed"}),
    "zip_prepare": frozenset({"zip_prepare_failed"}),
    "zip_export": frozenset({"zip_export_failed"}),
    "private_zip_export": frozenset({
        "private_zip_exported", "private_zip_export_failed", "private_zip_export_cancelled",
    }),
    "har_record": frozenset({"har_record_failed", "capture_interrupted", "playwright_missing"}),
    "har_replay": frozenset({"har_replay_failed", "playwright_missing"}),
}
RUNTIME_OPERATIONS = frozenset(RUNTIME_CODES_BY_OPERATION)
RUNTIME_CODES = frozenset().union(*RUNTIME_CODES_BY_OPERATION.values())
CODES = frozenset({
    "ready", "python_ready", "python_install_failed",
    "chromium_installed_unverified", "chromium_missing",
    "chromium_revision_incompatible", "playwright_missing",
    "linux_libraries_missing", "browser_launch_failed",
    "browser_cache_unavailable", "disk_space_insufficient",
    "venv_required", "download_failed", "download_timeout",
    "download_incomplete", "browser_download_source_unapproved",
    "cancelled", "installation_already_running", "unsupported_platform",
    "browser_probe_failed", "installer_unavailable",
    "invalid_install_options", "consent_required", "browser_operation_failed",
    "capture_failed", "capture_interrupted", "capture_limit",
    "review_failed", "zip_prepare_failed", "zip_export_failed",
    "har_record_failed", "har_replay_failed",
    "private_zip_exported", "private_zip_export_failed", "private_zip_export_cancelled",
})
ATTENTION = frozenset({
    "chromium_installed_unverified", "chromium_missing",
    "chromium_revision_incompatible", "playwright_missing",
    "cancelled", "installation_already_running", "consent_required",
    "private_zip_export_cancelled",
})
_PRIVATE_EXPORT_METRICS = {"duration_ms": 86_400_000, "output_bytes": 16 * 1024 * 1024}


def public_fields(operation: object, code: object, *, duration_ms=None,
                  output_bytes=None) -> dict[str, str | int] | None:
    if type(operation) is not str or operation not in OPERATIONS:
        return None
    safe_code = code if type(code) is str and code in CODES else "browser_operation_failed"
    fields: dict[str, str | int] = {"plugin_id": PLUGIN_ID, "operation": operation, "code": safe_code}
    if operation == "private_zip_export":
        for key, value in (("duration_ms", duration_ms), ("output_bytes", output_bytes)):
            if type(value) is int and 0 <= value <= _PRIVATE_EXPORT_METRICS[key]:
                fields[key] = value
    return fields


def public_status(fields: dict[str, str | int]) -> str:
    code = fields["code"]
    return ("ok" if code in {"ready", "python_ready", "private_zip_exported"}
            else "warning" if code in ATTENTION else "error")


def verified_fields(detail: object, operation: str, status: str) -> dict[str, str | int] | None:
    if type(detail) is not dict:
        return None
    base = {"plugin_id", "operation", "code"}
    if not base <= set(detail) or set(detail) - base - set(_PRIVATE_EXPORT_METRICS):
        return None
    if detail.get("plugin_id") != PLUGIN_ID:
        return None
    fields = public_fields(detail.get("operation"), detail.get("code"),
                           duration_ms=detail.get("duration_ms"),
                           output_bytes=detail.get("output_bytes"))
    if fields is None or fields != detail:
        return None
    if operation != OPERATIONS[fields["operation"]] or status != public_status(fields):
        return None
    return fields
