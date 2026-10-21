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
}
# Only explicit runtime user actions may be attested through the scoped plugin
# context. Plugin code is never authorized to impersonate Core provisioning.
RUNTIME_OPERATIONS = frozenset({"http_capture", "http_review", "zip_prepare", "zip_export"})
RUNTIME_CODES = frozenset({
    "capture_failed", "capture_interrupted", "capture_limit",
    "review_failed", "zip_prepare_failed", "zip_export_failed",
    "playwright_missing",
})
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
})
ATTENTION = frozenset({
    "chromium_installed_unverified", "chromium_missing",
    "chromium_revision_incompatible", "playwright_missing",
    "cancelled", "installation_already_running", "consent_required",
})


def public_fields(operation: object, code: object) -> dict[str, str] | None:
    if type(operation) is not str or operation not in OPERATIONS:
        return None
    safe_code = code if type(code) is str and code in CODES else "browser_operation_failed"
    return {"plugin_id": PLUGIN_ID, "operation": operation, "code": safe_code}


def public_status(fields: dict[str, str]) -> str:
    code = fields["code"]
    return ("ok" if code in {"ready", "python_ready"}
            else "warning" if code in ATTENTION else "error")


def verified_fields(detail: object, operation: str, status: str) -> dict[str, str] | None:
    if type(detail) is not dict or set(detail) != {"plugin_id", "operation", "code"}:
        return None
    if detail.get("plugin_id") != PLUGIN_ID:
        return None
    fields = public_fields(detail.get("operation"), detail.get("code"))
    if fields is None or fields["code"] != detail.get("code"):
        return None
    if operation != OPERATIONS[fields["operation"]] or status != public_status(fields):
        return None
    return fields
