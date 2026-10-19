"""#979: a failed ADEPT process with ok=true cannot prove an Adobe cause.

Every subprocess and vendor payload is synthetic; all secrets stay local.
"""
import json
import subprocess
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime


SECRET = "PRIVATE_LICENSE?token=HIDDEN_SESSION"


def run(monkeypatch, vendor, *, status=10, command=None, kind="acsm"):
    events = []
    runtime = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]}, logger=None,
        report_diagnostic=lambda *args, **kwargs: events.append((args, kwargs)),
    ))
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["mock"], status, json.dumps(vendor).encode(),
            SECRET.encode(),
        ),
    )
    with pytest.raises(KoboAdeptError) as caught:
        runtime.run_json(
            command or ["download", "request", "-", "--json"],
            payload={"source": {"type": kind}},
        )
    assert SECRET not in (str(caught.value) + repr(events)
                          + repr(caught.value.details))
    return caught.value


@pytest.mark.parametrize("vendor_code", [
    "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER",
    "adept_license_already_fulfilled_by_another_user",
    "profile_missing",
])
@pytest.mark.parametrize("exit_status", [1, 10])
def test_nonzero_exit_and_vendor_success_is_untrusted(monkeypatch, vendor_code, exit_status):
    result = run(monkeypatch, {
        "ok": True,
        "error": {"code": vendor_code, "message": SECRET, "retryable": True},
    }, status=exit_status)
    assert result.code == "kobo_adept_invalid_response"
    assert result.retryable is False
    assert result.exit_code == exit_status
    assert "autre identité" not in str(result)


def test_non_acsm_success_exit_conflict_is_not_retryable(monkeypatch):
    error = run(monkeypatch, {
        "ok": True, "error": {"code": "profile_missing", "message": SECRET},
    }, status=1, command=["profile", "list", "--json"], kind="http")
    assert error.code == "kobo_adept_invalid_response"
    assert not error.retryable


def test_explicit_failed_acsm_response_still_classifies_known_machine_code(monkeypatch):
    error = run(monkeypatch, {
        "ok": False, "error": {
            "code": "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER",
            "message": SECRET, "retryable": True,
        },
    }, status=10)
    assert error.code == "adept_license_already_fulfilled_by_another_user"
    assert error.retryable is False
