"""#979: actual runtime parser -> installed Core diagnostics, synthetic CLI.

No real CLI, provider, Adobe activation, license, storage or network access.
"""
import importlib
import json
from pathlib import Path
import subprocess

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from tests.test_adept_shared_diagnostics import install_runtime, application_log


CODES = ("invalid_acsm", "profile_not_found", "adept_profile_required",
         "adept_profile_error", "adept_profile_incompatible", "adept_error")
PRIVATE = "PRIVATE_979_SENTINEL"


@pytest.mark.parametrize("operation,code,expected,retryable,ok", [
    *[("profile", code, code, True, False) for code in CODES],
    *[("acsm", code, code, False, False) for code in CODES],
    ("acsm", "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER",
     "adept_license_already_fulfilled_by_another_user", False, False),
    ("profile", "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER", "kobo_adept_failed", False, False),
    ("http", "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER", "kobo_adept_failed", False, False),
    ("acsm", PRIVATE, "adept_error", False, False),
    ("profile", PRIVATE, "kobo_adept_failed", False, False),
    ("acsm", None, "adept_error", False, False),
    ("profile", None, "kobo_adept_failed", False, False),
    ("acsm", "invalid_acsm", "kobo_adept_invalid_response", False, True),
])
def test_cli_envelope_survives_runtime_and_both_core_channels(
        tmp_path, monkeypatch, application_log, operation, code, expected,
        retryable, ok):
    manager, context, diag = install_runtime(tmp_path)
    root = Path(__file__).resolve().parents[1] / "src/plugins/runtime-kobo-adept"
    monkeypatch.syspath_prepend(str(root))
    module = importlib.import_module("cifereca_kobo_adept_runtime")
    runtime = module.KoboAdeptRuntime(context)
    args = (["profile", "list", "--json"] if operation == "profile"
            else ["download", "request", "-", "--json"])
    payload = (None if operation == "profile" else
               {"source": {"type": operation, "private": PRIVATE}})
    calls = []
    response = {"ok": ok, "error": {"message": PRIVATE, "retryable": True}}
    if code is not None:
        response["error"]["code"] = code

    def synthetic(argv, **kwargs):
        calls.append(1)
        return subprocess.CompletedProcess(argv, 10, json.dumps(response).encode(),
                                           PRIVATE.encode())

    monkeypatch.setattr(module.subprocess, "run", synthetic)
    try:
        with pytest.raises(module.KoboAdeptError) as caught:
            runtime.run_json(args, payload=payload)
        assert caught.value.code == expected
        assert caught.value.retryable is retryable
        assert len(calls) == 1
        rows = [row for row in query_diagnostic_events(
            diag, log_path=application_log, limit=100,
        )["events"] if row.get("detail", {}).get("operation_kind") == "kobo_adept"]
        assert {row["source"] for row in rows} == {"core_events", "app_log"}
        assert len(rows) == 2
        assert rows[0]["detail"] == rows[1]["detail"]
        facts = rows[0]["detail"]
        assert facts["code"] == expected
        assert facts["status"] == "failed"
        assert facts["retryable"] is retryable
        assert facts["returncode"] == 10
        assert PRIVATE not in json.dumps(rows)
        assert PRIVATE not in application_log.read_text()
        assert PRIVATE not in str(caught.value) + repr(caught.value.details)
    finally:
        manager.close()
