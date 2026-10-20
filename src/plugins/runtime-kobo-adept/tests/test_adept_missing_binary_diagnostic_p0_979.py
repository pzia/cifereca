"""#979 P0: executable-not-found is a closed diagnostic, never argv leakage.

No actual CLI, ACSM, identity, private profile, or subprocess is touched.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as runtime_module
from cifereca.plugin_api import AttentionRequired
from cifereca_kobo_adept_runtime import KoboAdeptRuntime

PRIVATE_PATH = "/home/private/SECRET_PROFILE/kobo-adept"
PRIVATE_URL = "https://account.invalid/token?secret=PRIVATE"


def runtime(callback, *, logger=None):
    return KoboAdeptRuntime(SimpleNamespace(
        config={"command": [PRIVATE_PATH, "--token", PRIVATE_URL]},
        logger=logger, report_diagnostic=callback,
    ))


def test_missing_process_diagnostic_is_closed_and_preserves_error_type(monkeypatch):
    callbacks = []
    calls = []

    def missing(*args, **kwargs):
        calls.append((args, kwargs))
        raise FileNotFoundError(2, "PRIVATE_TOKEN missing binary", PRIVATE_PATH)

    monkeypatch.setattr(runtime_module.subprocess, "run", missing)
    r = runtime(lambda *args, **kw: callbacks.append((args, kw)))
    with pytest.raises(AttentionRequired) as caught:
        r.run_json(["profile", "show", "PRIVATE_PROFILE", "--json"])

    error = caught.value
    assert error.code == "kobo_adept_missing"
    assert len(calls) == 1
    assert len(callbacks) == 1
    args, kwargs = callbacks[0]
    assert args[1] == "error"
    assert kwargs["level"] == "normal"
    info = kwargs["detail"]
    assert info["error_code"] == "kobo_adept_missing"
    assert info["retryable"] is False
    assert type(info["duration_ms"]) is int and info["duration_ms"] >= 0
    assert "stdout_bytes" not in info and "stderr_bytes" not in info
    assert "launch_error" not in info
    assert r.last_run_diagnostic["error_code"] == "kobo_adept_missing"
    visible = repr((error, r.last_run_diagnostic, callbacks))
    assert "SECRET_PROFILE" not in visible
    assert "PRIVATE" not in visible
    assert "PRIVATE_TOKEN" not in visible
    assert PRIVATE_URL not in visible


def test_failure_of_diagnostic_callback_never_changes_cli_missing_exception(monkeypatch):
    messages = []

    class Logger:
        def debug(self, message):
            messages.append(message)

    def faulty_callback(*args, **kwargs):
        raise RuntimeError("PRIVATE_TOKEN " + PRIVATE_URL)

    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "SECRET_PROFILE", PRIVATE_PATH)

    monkeypatch.setattr(runtime_module.subprocess, "run", missing)
    r = runtime(faulty_callback, logger=Logger())
    with pytest.raises(AttentionRequired, match="installé") as caught:
        r.run_json(["contract", "--json"])
    assert caught.value.code == "kobo_adept_missing"
    assert messages == ["Kobo-Adept diagnostic reporter unavailable"]
    assert "PRIVATE" not in repr(messages)


def test_subsequent_success_does_not_republish_missing_cli_error(monkeypatch):
    callback = []
    r = runtime(lambda *args, **kw: callback.append((args, kw)))

    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "SECRET_PROFILE", PRIVATE_PATH)

    monkeypatch.setattr(runtime_module.subprocess, "run", missing)
    with pytest.raises(AttentionRequired):
        r.run_json(["capabilities", "--json"])

    monkeypatch.setattr(
        runtime_module.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["kobo-adept"], 0, json.dumps({"ok": True}).encode("utf-8"), b"",
        ),
    )
    assert r.run_json(["capabilities", "--json"]) == {"ok": True}
    assert len(callback) == 2
    assert callback[0][0][1] == "error"
    assert callback[1][0][1] == "ok"
    assert "error_code" not in r.last_run_diagnostic
    assert "retryable" not in r.last_run_diagnostic
    assert "error_code" not in callback[1][1]["detail"]
