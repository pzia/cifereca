"""P0 #979: a CLI exit 0 is never proof of an ADEPT operation's success.

Only synthetic, offline JSON and stderr. No ACSM, account, Adobe profile,
license, device, subprocess execution or signed URL is accessed.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime

PRIVATE = (
    "PRIVATE_LICENSE",
    "PRIVATE_SESSION",
    "alice@example.invalid",
    "https://vendor.invalid/fulfill?token=PRIVATE_SESSION",
    "<fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>",
)
RAW = " ".join(PRIVATE)

OPERATIONS = (
    (["contract", "--json"], None),
    (["capabilities", "--json"], None),
    (["profile", "list", "--json"], None),
    (["profile", "add", "alice", "--json"], None),
    (["acsm", "return", "loan", "--json"], None),
    (["download", "request", "-", "--json"], {"source": {"type": "acsm"}}),
    (["download", "request", "-", "--json"], {"source": {"type": "http"}}),
)


def run(monkeypatch, args, request, reply, *, exit_code=0):
    reports = []
    context = SimpleNamespace(
        config={"command": ["kobo-adept"]},
        logger=None,
        report_diagnostic=lambda *args, **kwargs: reports.append((args, kwargs)),
    )
    stdout = json.dumps(reply, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["synthetic"], exit_code, stdout, RAW.encode("utf-8"),
        ),
    )
    runtime = KoboAdeptRuntime(context)
    try:
        result = runtime.run_json(args, payload=request)
    except KoboAdeptError as exc:
        published = (
            str(exc) + str(exc.code) + exc.technical_detail
            + repr(exc.details) + repr(runtime.last_run_diagnostic)
            + repr(reports)
        )
        for secret in PRIVATE:
            assert secret not in published
        assert exc.exit_code == exit_code
        assert exc.retryable is False
        assert reports[-1][0][1] == "error"
        return exc, runtime
    assert reports[-1][0][1] == "ok"
    return result, runtime


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("bad_ack", [
    None, "true", "false", 1, 0, 2, [], {}, "", "PRIVATE_LICENSE",
])
def test_zero_exit_without_exact_true_is_never_a_success(
        monkeypatch, operation, bad_ack):
    args, request = operation
    result, runtime = run(
        monkeypatch, args, request,
        {"ok": bad_ack, "profile": {"name": "alice"},
         "loan_id": "synthetic", "secret": RAW},
    )
    assert isinstance(result, KoboAdeptError)
    assert result.code == "kobo_adept_invalid_response"
    assert runtime.last_run_diagnostic["stdout_mode"] == "json"


@pytest.mark.parametrize("operation", OPERATIONS)
def test_missing_ack_does_not_authorize_any_cli_operation(monkeypatch, operation):
    result, runtime = run(
        monkeypatch, *operation,
        {"profile": {"complete": True}, "returned": True, "secret": RAW},
    )
    assert isinstance(result, KoboAdeptError)
    assert result.code == "kobo_adept_invalid_response"


@pytest.mark.parametrize("vendor_error", [
    {"code": "profile_missing", "message": RAW},
    {"code": "adept_error", "message": RAW},
    RAW, ["PRIVATE_LICENSE"], False, 0,
])
@pytest.mark.parametrize("operation", OPERATIONS)
def test_success_with_non_null_vendor_error_is_contradictory(
        monkeypatch, operation, vendor_error):
    result, _runtime = run(
        monkeypatch, *operation,
        {"ok": True, "error": vendor_error, "returned": True},
    )
    assert isinstance(result, KoboAdeptError)
    assert result.code == "kobo_adept_invalid_response"
    assert "contradictoire" in str(result)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_explicit_success_and_null_error_still_work(monkeypatch, operation):
    response, runtime = run(
        monkeypatch, *operation,
        {"ok": True, "error": None, "metadata": {"synthetic": True}},
    )
    assert response["ok"] is True
    assert runtime.last_run_diagnostic["returncode"] == 0


@pytest.mark.parametrize("operation", OPERATIONS)
def test_explicit_success_without_error_field_still_works(monkeypatch, operation):
    response, _runtime = run(
        monkeypatch, *operation,
        {"ok": True, "metadata": {"synthetic": True}},
    )
    assert response["ok"] is True


def test_negative_ack_with_zero_exit_remains_a_failure(monkeypatch):
    response, _ = run(
        monkeypatch, ["profile", "show", "alice", "--json"], None,
        {"ok": False, "error": {"code": "profile_missing", "message": RAW}},
    )
    assert isinstance(response, KoboAdeptError)
    assert response.code == "profile_missing"


def test_negative_acsm_ack_with_zero_exit_does_not_prove_loan_rights(monkeypatch):
    response, _ = run(
        monkeypatch, ["download", "request", "-", "--json"],
        {"source": {"type": "acsm"}},
        {"ok": False, "error": {"code": "adept_error", "message": RAW}},
    )
    assert isinstance(response, KoboAdeptError)
    assert response.code == "adept_error"


def test_exit_nonzero_with_true_ack_is_still_a_failure(monkeypatch):
    response, _ = run(
        monkeypatch, ["profile", "show", "alice", "--json"], None,
        {"ok": True, "profile": {"complete": True}}, exit_code=1,
    )
    assert isinstance(response, KoboAdeptError)
    # Exit nonzero contradicts ok=true. It is an invalid CLI receipt, not
    # evidence for a more specific vendor failure or a successful mutation.
    assert response.code == "kobo_adept_invalid_response"
    assert response.retryable is False


def test_exit_zero_typed_true_but_error_null_does_not_gain_rights_implicitly(monkeypatch):
    result, _ = run(
        monkeypatch, ["download", "request", "-", "--json"],
        {"source": {"type": "acsm"}},
        {"ok": True, "error": None, "artifact": None},
    )
    # The runtime's transport only acknowledges successful JSON; acquisition
    # must independently prove a valid artifact later in the call chain.
    assert result["artifact"] is None
    assert result["ok"] is True
