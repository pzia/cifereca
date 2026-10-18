"""P0 #979: untrusted Kobo-Adept JSON code/message never becomes a Core secret.

The process is simulated. No real ACSM, Adobe profile, credentials or files.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptRuntime, KoboAdeptError

SECRETS = (
    "secret-person@example.invalid",
    "https://server.invalid/acsms?token=PRIVATE_SESSION",
    "<fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>",
    "PRIVATE_SESSION", "PRIVATE_LICENSE",
)
MESSAGE = " ".join(SECRETS)


def fail(monkeypatch, *, code, operation=None, source_type="http", exit_code=1,
         retryable=True, nested=True, message=MESSAGE):
    emitted = []
    context = SimpleNamespace(
        config={"command": ["kobo-adept"]}, logger=None,
        report_diagnostic=lambda *args, **kwargs: emitted.append((args, kwargs)),
    )
    runtime = KoboAdeptRuntime(context)
    error = {"code": code, "message": message, "retryable": retryable}
    payload = {"ok": False, "error": error} if nested else {"ok": False, **error}
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["fake"], exit_code, json.dumps(payload).encode(), MESSAGE.encode(),
        ),
    )
    args = operation or ["download", "request", "-", "--json"]
    with pytest.raises(KoboAdeptError) as caught:
        runtime.run_json(args, payload={"source": {"type": source_type}})
    exc = caught.value
    published = (
        str(exc) + repr(exc.code) + repr(exc.details) + exc.technical_detail
        + repr(runtime.last_run_diagnostic) + repr(emitted)
    )
    for private in SECRETS:
        assert private not in published
    assert emitted[-1][0][1] == "error"
    assert runtime.last_run_diagnostic["operation"] in {
        "download request", "acsm return", "profile show", "profile list",
        "other", "contract",
    }
    assert exc.exit_code == exit_code
    return exc


@pytest.mark.parametrize("code", [
    "unexpected_code", "password=PRIVATE_SESSION",
    "token: PRIVATE_LICENSE", "secret-person@example.invalid",
    "https://server.invalid/acsms?token=PRIVATE_SESSION",
    "<fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>",
    [], ["profile_missing"], {"code": "profile_missing"}, None, 0, 100,
])
@pytest.mark.parametrize("operation", [
    ["download", "request", "-", "--json"],
    ["acsm", "return", "loan", "--json"],
    ["profile", "show", "fake", "--json"],
    ["contract", "--json"],
])
def test_unknown_vendor_error_codes_do_not_reach_user_or_core(
        monkeypatch, code, operation):
    result = fail(monkeypatch, code=code, operation=operation)
    assert result.code == "kobo_adept_failed"
    assert result.retryable is False
    assert "code fournisseur non qualifié" in str(result)


@pytest.mark.parametrize("code", ["profile_missing", "invalid_profile"])
def test_known_profile_errors_keep_exact_code_but_not_untrusted_message(
        monkeypatch, code):
    result = fail(
        monkeypatch, code=code, operation=["profile", "show", "fake", "--json"],
    )
    assert result.code == code
    assert result.retryable is True
    assert ("introuvable" if code == "profile_missing" else "invalide") in str(result)


@pytest.mark.parametrize("code", ["profile_missing", "invalid_profile"])
def test_non_retryable_known_profile_errors_stay_non_retryable(monkeypatch, code):
    result = fail(monkeypatch, code=code, retryable=False)
    assert result.code == code
    assert result.retryable is False


def test_untrusted_top_level_vendor_code_is_not_interpreted(monkeypatch):
    result = fail(monkeypatch, code="profile_missing", nested=False)
    assert result.code == "kobo_adept_failed"
    assert result.retryable is False


def test_real_acsm_operation_preserves_exact_adobe_code_only(monkeypatch):
    exact = fail(
        monkeypatch, source_type="acsm",
        code="E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER", exit_code=10,
    )
    assert exact.code == "adept_license_already_fulfilled_by_another_user"
    assert exact.retryable is False
    opaque = fail(
        monkeypatch, source_type="acsm",
        code="password=PRIVATE_SESSION", exit_code=10,
    )
    assert opaque.code == "adept_error"
    assert opaque.retryable is False


def test_known_non_acsm_code_with_raw_xml_message_never_leaks(monkeypatch):
    result = fail(
        monkeypatch, code="profile_missing", source_type="http",
        message="<fulfillmentToken>PRIVATE_LICENSE</fulfillmentToken>",
    )
    assert result.code == "profile_missing"
    assert "PRIVATE_LICENSE" not in str(result)
