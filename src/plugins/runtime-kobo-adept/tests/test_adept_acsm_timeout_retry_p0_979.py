"""#979 P0: timed-out one-shot ACSM fulfillment must not suggest retry.

Synthetic subprocess timeouts only. No real ACSM, profile or network.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as runtime_module
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime

PRIVATE = "https://licenses.invalid/fulfill?token=PRIVATE"
XML = "<fulfillmentToken>PRIVATE_TOKEN</fulfillmentToken>"


@pytest.mark.parametrize("args,payload,expected_retryable", [
    (["download", "request", "-", "--json"],
     {"source": {"type": "acsm", "url": PRIVATE}}, False),
    (["download", "request", "-", "--json"],
     {"source": {"type": "http", "url": PRIVATE}}, True),
    (["download", "request", "-", "--json"],
     {"source": {"type": "kobo"}}, True),
    (["profile", "list", "--json"], None, True),
    (["acsm", "return", "loan-id", "--json"], None, True),
    (["download", "request", "-", "--json"],
     {"source": {"type": "ACSM"}}, True),
    (["download", "request", "-", "--json"],
     {"source": {"type": "acsm"}}, False),
])
def test_timeout_retryability_is_contextual_and_never_parses_stderr(
        monkeypatch, args, payload, expected_retryable):
    events = []

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["kobo-adept", PRIVATE], timeout=1,
            output=(XML + " " + PRIVATE).encode("utf-8"),
            stderr=(PRIVATE + " " + XML).encode("utf-8"),
        )

    monkeypatch.setattr(runtime_module.subprocess, "run", timeout)
    runtime = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept", "--private", PRIVATE]},
        logger=None,
        report_diagnostic=lambda *a, **kw: events.append((a, kw)),
    ))
    with pytest.raises(KoboAdeptError) as raised:
        runtime.run_json(args, payload=payload)
    error = raised.value
    assert error.code == "kobo_adept_timeout"
    assert error.retryable is expected_retryable
    assert error.exit_code is None
    assert runtime.last_run_diagnostic["retryable"] is expected_retryable
    assert runtime.last_run_diagnostic["stdout_mode"] == "timeout"
    assert runtime.last_run_diagnostic["error_code"] == "kobo_adept_timeout"
    assert len(events) == 1
    assert events[0][0][1] == "error"
    assert events[0][1]["detail"]["retryable"] is expected_retryable
    public = repr((error, error.details, error.technical_detail, events))
    for secret in ("PRIVATE", "fulfillmentToken", "PRIVATE_TOKEN",
                   "licenses.invalid"):
        assert secret not in public


def test_next_success_clears_timeout_diagnostic(monkeypatch):
    events = []
    runtime = KoboAdeptRuntime(SimpleNamespace(
        config={"command": ["kobo-adept"]},
        logger=None,
        report_diagnostic=lambda *a, **kw: events.append((a, kw)),
    ))

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["kobo-adept"], timeout=1)

    monkeypatch.setattr(runtime_module.subprocess, "run", timeout)
    with pytest.raises(KoboAdeptError):
        runtime.run_json(["download", "request", "-", "--json"],
                         payload={"source": {"type": "acsm"}})
    assert runtime.last_run_diagnostic["retryable"] is False

    monkeypatch.setattr(runtime_module.subprocess, "run", lambda *_a, **_kw:
        subprocess.CompletedProcess(["kobo-adept"], 0, b'{"ok":true}', b''))
    assert runtime.run_json(["capabilities", "--json"]) == {"ok": True}
    assert "retryable" not in runtime.last_run_diagnostic
    assert "error_code" not in runtime.last_run_diagnostic
    assert events[-1][0][1] == "ok"
    assert "retryable" not in events[-1][1]["detail"]
