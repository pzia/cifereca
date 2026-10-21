"""Closed, CoreUI-owned Browser Recorder diagnostics (#2663).

Only an explicit Plugins action may call this adapter. Never forward arbitrary
plugin/site payloads, subprocess text, browser paths or exception messages.
The existing Core Diagnostics collector is the sole event store and provides
the public broker feed; there is no private HAR/session log.
"""
from __future__ import annotations

_PLUGIN_ID = "ui.page.browser-recorder"
_OPERATIONS = {
    "python_dependencies_install": "browser recorder python dependencies",
    "chromium_probe": "browser recorder chromium verification",
    "chromium_install": "browser recorder chromium installation",
}
_CODES = frozenset({
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
})
_ATTENTION = frozenset({
    "chromium_installed_unverified", "chromium_missing",
    "chromium_revision_incompatible", "playwright_missing",
    "cancelled", "installation_already_running", "consent_required",
})


def emit_browser_recorder_diagnostic(window: object, operation: str, code: str) -> None:
    """Record an opt-in operation outcome using allowlisted public fields.

    Absence/closure of an optional diagnostics collector never changes a
    provisioning result. This function makes no network/disk/UI operations.
    """
    if type(operation) is not str or operation not in _OPERATIONS:
        return
    safe_code = code if type(code) is str and code in _CODES else "browser_operation_failed"
    status = ("ok" if safe_code in {"ready", "python_ready"}
              else "warning" if safe_code in _ATTENTION else "error")
    try:
        diagnostics = getattr(window, "diagnostics", None)
        if diagnostics is None:
            return
        add = getattr(diagnostics, "add", None)
        if not callable(add):
            return
        add(
            "PLUGIN", _OPERATIONS[operation], status,
            detail={
                "plugin_id": _PLUGIN_ID,
                "operation": operation,
                "code": safe_code,
            },
            level="normal",
        )
    except Exception:
        # Do not convert a diagnostic failure into another user-visible error.
        # In particular, never stringify a potentially private exception.
        return
