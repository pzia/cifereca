"""P0 #979: OS launch failures must not export exception subclass names.

Only a fake subprocess is used; no external CLI or private account.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as runtime_module
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime


SECRET = "PRIVATE_ADOBE_PROFILE_TOKEN"
PRIVATE_URL = "https://licenses.invalid/fulfill?token=PRIVATE_VALUE"
PrivateNamedOSError = type("OSError_" + SECRET, (OSError,), {})


@pytest.mark.parametrize("failure_type", [OSError, PermissionError, PrivateNamedOSError])
def test_os_launch_failure_exports_only_closed_diagnostic_fields(monkeypatch, failure_type):
    reports = []
    invocations = []

    def failing_process(*args, **kwargs):
        invocations.append((args, kwargs))
        raise failure_type("private CLI launch error " + PRIVATE_URL + " " + SECRET)

    monkeypatch.setattr(runtime_module.subprocess, "run", failing_process)
    runtime = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["/home/SECRET_PROFILE/kobo-adept"]},
        logger=None, report_diagnostic=lambda *a, **kw: reports.append((a, kw)),
    ))

    with pytest.raises(KoboAdeptError) as caught:
        runtime.run_json(["profile", "list", "--json"])

    error = caught.value
    assert error.code == "kobo_adept_launch_error"
    assert error.retryable is False
    assert len(invocations) == 1
    assert len(reports) == 1
    args, kwargs = reports[0]
    assert args[1] == "error"
    assert kwargs["detail"]["error_code"] == "kobo_adept_launch_error"
    assert kwargs["detail"]["launch_error"] == "os_error"
    assert kwargs["detail"]["retryable"] is False
    assert kwargs["detail"]["operation"] == "profile"
    assert kwargs["detail"]["duration_ms"] >= 0
    assert runtime.last_run_diagnostic["launch_error"] == "os_error"

    exported = json.dumps({
        "error": str(error), "details": error.details,
        "technical_detail": error.technical_detail,
        "diagnostic": runtime.last_run_diagnostic, "reports": reports,
    }, default=str)
    for forbidden in (SECRET, PRIVATE_URL, "PRIVATE_VALUE", "SECRET_PROFILE",
                      failure_type.__name__ if failure_type is PrivateNamedOSError else SECRET):
        assert forbidden not in exported
