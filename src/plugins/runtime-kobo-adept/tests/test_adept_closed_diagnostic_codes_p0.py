"""#979 P0: Kobo-ADEPT diagnostic callback exposes safe machine codes only.

No Adobe account, ACSM, process execution, filesystem or user session.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace as NS

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptRuntime, KoboAdeptError

PRIVATE = "https://private.invalid/acsms?token=PRIVATE_SESSION"
SECRET = "<fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>"


def runtime_with_reply(monkeypatch, payload, *, exit_code=10, raw=False):
    received = []
    runtime = KoboAdeptRuntime(NS(
        config={"command": ["kobo-adept"]},
        logger=None,
        report_diagnostic=lambda *args, **kw: received.append((args, kw)),
    ))
    result = payload if raw else json.dumps(payload).encode()
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *_a, **_kw: subprocess.CompletedProcess(
            ["kobo-adept"], exit_code, result, (PRIVATE + SECRET).encode(),
        ),
    )
    return runtime, received


@pytest.mark.parametrize("payload,args,source,exit_code,code,retryable", (
    ({"ok": False, "error": {
        "code": "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER",
        "message": PRIVATE, "retryable": True,
    }}, ["download", "request", "-", "--json"], "acsm", 10,
     "adept_license_already_fulfilled_by_another_user", False),
    ({"ok": False, "error": {
        "code": PRIVATE, "message": SECRET, "retryable": True,
    }}, ["download", "request", "-", "--json"], "acsm", 10,
     "adept_error", False),
    ({"ok": False, "error": {
        "code": "profile_missing", "message": SECRET, "retryable": True,
    }}, ["profile", "show", "fake", "--json"], None, 1,
     "profile_missing", True),
    ({"ok": False, "error": {
        "code": PRIVATE, "message": SECRET, "retryable": True,
    }}, ["profile", "show", "fake", "--json"], None, 1,
     "kobo_adept_failed", False),
))
def test_closed_code_and_retryable_are_consistent_across_exception_and_diagnostics(
    monkeypatch, payload, args, source, exit_code, code, retryable,
):
    runtime, published = runtime_with_reply(monkeypatch, payload, exit_code=exit_code)
    kwargs = {"payload": {"source": {"type": source}}} if source else {}
    with pytest.raises(KoboAdeptError) as caught:
        runtime.run_json(args, **kwargs)
    err = caught.value
    assert err.code == code
    assert err.retryable is retryable
    assert runtime.last_run_diagnostic["error_code"] == code
    assert runtime.last_run_diagnostic["retryable"] is retryable
    assert err.details["error_code"] == code
    assert err.details["retryable"] is retryable
    assert len(published) == 1
    event, kw = published[0]
    assert event[1] == "error"
    assert kw["detail"]["error_code"] == code
    assert kw["detail"]["retryable"] is retryable
    exposed = repr((err, err.details, err.technical_detail, published))
    assert PRIVATE not in exposed
    assert "PRIVATE_SESSION" not in exposed
    assert "PRIVATE_LICENSE" not in exposed
    assert "fulfillmentToken" not in exposed


def test_invalid_json_yields_diagnostic_code_without_echoing_stdout(monkeypatch):
    runtime, received = runtime_with_reply(
        monkeypatch, b'\\xffPRIVATE_SESSION', exit_code=10, raw=True,
    )
    with pytest.raises(KoboAdeptError) as caught:
        runtime.run_json(["download", "request", "-", "--json"],
                         payload={"source": {"type": "acsm"}})
    # Invalid bytes take the UTF-8 decoding path before JSON parsing.
    assert caught.value.code == "kobo_adept_invalid_utf8"
    assert received[-1][1]["detail"]["error_code"] == "kobo_adept_invalid_utf8"
    assert received[-1][1]["detail"]["retryable"] is False
    assert "PRIVATE" not in repr(received)


def test_success_after_failure_does_not_keep_old_error_code(monkeypatch):
    runtime, received = runtime_with_reply(
        monkeypatch,
        {"ok": False, "error": {"code": "unexpected", "message": PRIVATE}},
        exit_code=1,
    )
    with pytest.raises(KoboAdeptError):
        runtime.run_json(["profile", "show", "fake", "--json"])
    assert received[-1][1]["detail"]["error_code"] == "kobo_adept_failed"

    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *_a, **_kw: subprocess.CompletedProcess(
            ["kobo-adept"], 0, b'{"ok":true}', b"",
        ),
    )
    assert runtime.run_json(["capabilities", "--json"]) == {"ok": True}
    assert "error_code" not in runtime.last_run_diagnostic
    assert "retryable" not in runtime.last_run_diagnostic
    assert "error_code" not in received[-1][1]["detail"]
