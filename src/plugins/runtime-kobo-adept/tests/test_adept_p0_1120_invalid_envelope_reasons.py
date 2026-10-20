"""P0 #1120: read-only simulated Kobo-ADEPT loans-list response diagnosis.

No real CLI, profile, loan, ACSM, ADEPT, Adobe or Paris connection is made.
The contract for a failed response and its retry policy must remain unchanged.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace as NS

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime

PRIVATE_PROFILE = "PRIVATE_PROFILE_ABC"
PRIVATE_URL = "https://private.invalid/fulfill?token=PRIVATE_SESSION"
PRIVATE_LICENSE = "<fulfillmentToken>PRIVATE_KEY</fulfillmentToken>"


def fixture(monkeypatch, json_value, *, returncode=0, stderr=b""):
    observed = []
    invocations = []
    runtime = KoboAdeptRuntime(NS(
        config={"command": ["kobo-adept"]}, logger=None,
        report_diagnostic=lambda *a, **kw: observed.append((a, kw)),
    ))
    out = json.dumps(json_value).encode("utf-8")
    def fake_subprocess(argv, **kwargs):
        invocations.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, returncode, out, stderr)
    monkeypatch.setattr(module.subprocess, "run", fake_subprocess)
    return runtime, observed, invocations, out


@pytest.mark.parametrize("response,exit_code,reason", [
    ([], 0, "json_not_object"),
    (None, 0, "json_not_object"),
    ({"loans": [], "private": PRIVATE_URL}, 0, "ok_missing"),
    ({"ok": 1, "private": PRIVATE_URL}, 0, "ok_not_boolean"),
    ({"ok": "true", "private": PRIVATE_LICENSE}, 0, "ok_not_boolean"),
    ({"ok": None, "private": PRIVATE_URL}, 0, "ok_not_boolean"),
    ({"ok": True, "error": {"secret": PRIVATE_URL}}, 0, "success_with_error"),
    ({"ok": True, "error": PRIVATE_LICENSE}, 0, "success_with_error"),
    ({"ok": True, "loans": []}, 1, "exit_nonzero_with_success"),
])
def test_invalid_envelopes_emit_closed_reason_without_vendor_content(
    monkeypatch, response, exit_code, reason,
):
    instance, published, calls, stdout = fixture(
        monkeypatch, response, returncode=exit_code,
        stderr=(PRIVATE_LICENSE + PRIVATE_URL).encode(),
    )
    args = ["loans", "list", "--profile", PRIVATE_PROFILE, "--json"]
    with pytest.raises(KoboAdeptError) as caught:
        instance.run_json(args, max_output_bytes=4 * 1024 * 1024)
    err = caught.value
    assert err.code == "kobo_adept_invalid_response"
    assert err.retryable is False
    assert err.details["error_code"] == "kobo_adept_invalid_response"
    assert err.details["validation_reason"] == reason
    assert err.details["stdout_bytes"] == len(stdout)
    assert err.details["returncode"] == exit_code
    assert len(published) == len(calls) == 1
    call, kwargs = calls[0]
    assert call == ["kobo-adept", *args]
    assert kwargs["input"] is None
    assert kwargs["capture_output"] is True
    detail = published[0][1]["detail"]
    assert detail["operation"] == "loans list"
    assert detail["error_code"] == "kobo_adept_invalid_response"
    assert detail["validation_reason"] == reason
    assert detail["retryable"] is False
    public = (
        repr(detail) + err.technical_detail + repr(instance.last_run_diagnostic)
        + str(err)
    )
    for private in (PRIVATE_URL, PRIVATE_LICENSE, PRIVATE_PROFILE,
                    "PRIVATE_KEY", "PRIVATE_SESSION"):
        assert private not in public


@pytest.mark.parametrize("response,args", [
    ({"ok": True, "loans": []},
     ["loans", "list", "--profile", PRIVATE_PROFILE, "--json"]),
    ({"ok": False, "error": {"code": "profile_missing", "message": PRIVATE_URL}},
     ["loans", "list", "--profile", PRIVATE_PROFILE, "--json"]),
    ({"name": "kobo-adept", "contract_version": 8},
     ["contract", "--json"]),
])
def test_valid_receipts_and_descriptive_contract_have_no_validation_failure(
    monkeypatch, response, args,
):
    runtime, published, calls, _ = fixture(
        monkeypatch, response,
        returncode=0,
    )
    if response.get("ok") is False:
        with pytest.raises(KoboAdeptError) as caught:
            runtime.run_json(args)
        assert caught.value.code != "kobo_adept_invalid_response"
    else:
        result = runtime.run_json(args)
        assert result == response
    assert "validation_reason" not in runtime.last_run_diagnostic
    assert len(calls) == 1
    if published:
        assert "validation_reason" not in published[-1][1]["detail"]


@pytest.mark.parametrize("reason", [
    PRIVATE_URL, PRIVATE_LICENSE, 1, False, None, ["ok_missing"],
    {"reason": PRIVATE_URL}, object(),
])
def test_reporter_rejects_forged_or_non_closed_reason(reason):
    published = []
    runtime = KoboAdeptRuntime(NS(
        config={"command": ["kobo-adept"]},
        report_diagnostic=lambda *a, **kw: published.append(kw),
        logger=None,
    ))
    runtime.last_run_diagnostic = {
        "operation": "loans list", "validation_reason": reason,
        "error_code": "kobo_adept_invalid_response",
        "retryable": False,
    }
    runtime._report("Kobo-Adept loans list", "error")
    assert len(published) == 1
    assert "validation_reason" not in published[0]["detail"]


def test_cause_is_cleared_between_operations_and_never_copied_to_other_errors(
    monkeypatch,
):
    runtime, published, calls, _ = fixture(monkeypatch, {"loans": []})
    with pytest.raises(KoboAdeptError):
        runtime.run_json(["loans", "list", "--profile", PRIVATE_PROFILE, "--json"])
    assert runtime.last_run_diagnostic["validation_reason"] == "ok_missing"
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, b'{"ok":true,"loans":[]}', b"",
        ),
    )
    assert runtime.run_json(["loans", "list", "--profile", PRIVATE_PROFILE, "--json"])["ok"] is True
    assert "validation_reason" not in runtime.last_run_diagnostic
    runtime.last_run_diagnostic = {
        "operation": "loans list", "validation_reason": "ok_missing",
        "error_code": "kobo_adept_timeout", "retryable": False,
    }
    runtime._report("synthetic", "error")
    assert "validation_reason" not in published[-1][1]["detail"]
