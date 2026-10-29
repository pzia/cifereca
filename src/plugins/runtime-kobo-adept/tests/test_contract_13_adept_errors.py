"""Contract 1.3 §4 structured ADEPT codes, synthetic CLI only (#979)."""
import json
import subprocess
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime


CODES = (
    "invalid_acsm", "profile_not_found", "adept_profile_required",
    "adept_profile_error", "adept_profile_incompatible", "adept_error",
)
PRIVATE = '<fulfillmentToken>SECRET_TOKEN</fulfillmentToken>'


def failure(monkeypatch, code, *, acsm=False, retryable=False, ok=False,
            exit_code=10, args=None):
    events = []
    runtime = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]}, logger=None,
        report_diagnostic=lambda *a, **k: events.append((a, k)),
    ))
    payload = dict(ok=ok, error=dict(
        code=code, message=PRIVATE, retryable=retryable, exit_code=exit_code,
    ))
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k:
        subprocess.CompletedProcess(["synthetic"], exit_code,
                                    json.dumps(payload).encode(), PRIVATE.encode()))
    if args is None:
        args = (["download", "request", "-", "--json"] if acsm else
                ["profile", "show", "synthetic", "--json"])
    request = {"source": {"type": "acsm"}} if acsm else None
    with pytest.raises(KoboAdeptError) as caught:
        runtime.run_json(args, payload=request)
    error = caught.value
    assert PRIVATE not in str(error) + repr(error.details) + repr(events)
    assert error.exit_code == exit_code
    assert runtime.last_run_diagnostic["error_code"] == error.code
    assert runtime.last_run_diagnostic["retryable"] is error.retryable
    return error


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("retryable", [False, True, "true", 1, None])
def test_contract_code_survives_exit_10_without_parsing_message(
    monkeypatch, code, retryable,
):
    result = failure(monkeypatch, code, retryable=retryable)
    assert result.code == code
    assert result.retryable is (retryable is True)


@pytest.mark.parametrize("code", CODES)
def test_acsm_failure_keeps_qualified_cause_but_never_promises_safe_retry(
    monkeypatch, code,
):
    result = failure(monkeypatch, code, acsm=True, retryable=True)
    assert result.code == code
    assert result.retryable is False


@pytest.mark.parametrize("code", CODES)
def test_contradictory_success_with_nonzero_exit_cannot_supply_a_cause(
    monkeypatch, code,
):
    result = failure(monkeypatch, code, ok=True)
    assert result.code == "kobo_adept_invalid_response"
    assert result.retryable is False


def test_exit_10_unknown_code_does_not_become_adobe_identity_diagnosis(monkeypatch):
    result = failure(monkeypatch, "unqualified_" + PRIVATE, retryable=True)
    assert result.code == "kobo_adept_failed"
    assert result.retryable is False


@pytest.mark.parametrize("code,exit_code", [
    ("invalid_acsm", 3), ("profile_not_found", 5),
    ("adept_profile_incompatible", 10),
])
def test_contract_exit_category_does_not_erase_structured_cause(
    monkeypatch, code, exit_code,
):
    result = failure(monkeypatch, code, acsm=True, exit_code=exit_code)
    assert result.code == code
    assert result.retryable is False


def test_direct_acsm_fetch_never_promises_safe_retry(monkeypatch):
    result = failure(
        monkeypatch, "adept_error", retryable=True,
        args=["acsm", "fetch", "synthetic.acsm", "--json"],
    )
    assert result.code == "adept_error" and result.retryable is False


def test_direct_acsm_fetch_timeout_is_not_a_safe_retry(monkeypatch):
    runtime = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]}, logger=None,
        report_diagnostic=lambda *a, **k: None,
    ))

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic", 1, output=b"", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", timeout)
    with pytest.raises(KoboAdeptError) as caught:
        runtime.run_json(["acsm", "fetch", "synthetic.acsm", "--json"])
    assert caught.value.code == "kobo_adept_timeout"
    assert caught.value.retryable is False
