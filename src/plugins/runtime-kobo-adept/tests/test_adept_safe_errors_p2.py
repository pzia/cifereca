"""#979 — only explicit JSON error codes; never parse vendor stderr/XML."""
import json
import subprocess
from types import SimpleNamespace

import pytest

import cifereca_kobo_adept_runtime as runtime_module
from cifereca_kobo_adept_runtime import KoboAdeptError, KoboAdeptRuntime

SECRET = "secret-identity@example.test"
PRIVATE = "https://licenses.example.org/fulfill?token=PRIVATE_SESSION"
XML = "<fulfillmentToken>PRIVATE_ADOBE_TOKEN</fulfillmentToken>"


def run_failure(monkeypatch, payload, *, exit_code=10):
    events = []
    context = SimpleNamespace(
        config={"command": ["kobo-adept"]},
        logger=None,
        report_diagnostic=lambda *args, **kwargs: events.append((args, kwargs)),
    )
    runtime = KoboAdeptRuntime(context)
    stdout = json.dumps(payload).encode("utf-8")
    stderr = (XML + " " + PRIVATE + " " + SECRET).encode("utf-8")
    monkeypatch.setattr(
        runtime_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["fake"], exit_code, stdout, stderr,
        ),
    )
    with pytest.raises(KoboAdeptError) as raised:
        runtime.run_json(["download", "request", "-", "--json"], payload={"source": {"type": "acsm"}})
    error = raised.value
    public = str(error) + repr(error.details) + error.technical_detail + repr(events)
    for forbidden in (SECRET, PRIVATE, "PRIVATE_SESSION", "PRIVATE_ADOBE_TOKEN", "fulfillmentToken"):
        assert forbidden not in public
    assert error.exit_code == exit_code
    assert runtime.last_run_diagnostic["returncode"] == exit_code
    assert events and events[-1][0][1] == "error"
    return error


def test_structured_vendor_enum_maps_to_stable_actionable_code(monkeypatch):
    error = run_failure(monkeypatch, {
        "ok": False,
        "error": {
            "code": "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER",
            "message": f"libgourou failed: {PRIVATE} {SECRET} {XML}",
            "retryable": True,
        },
    })
    assert error.code == "adept_license_already_fulfilled_by_another_user"
    assert error.retryable is False
    assert "autre identité Adobe" in str(error)


def test_legacy_opaque_exit_10_never_leaks_message_or_guesses_cause(monkeypatch):
    error = run_failure(monkeypatch, {
        "ok": False,
        "error": {
            "code": "adept_error", "message": PRIVATE + " " + SECRET + " " + XML,
            "retryable": True,
        },
    })
    assert error.code == "adept_error"
    assert error.retryable is False
    assert "autre identité" not in str(error)


def test_old_top_level_cli_error_and_unrecognized_code_are_generic(monkeypatch):
    top = run_failure(monkeypatch, {
        "ok": False, "code": "adept_error", "message": PRIVATE,
    })
    assert top.code == "adept_error"
    assert top.retryable is False
    unknown = run_failure(monkeypatch, {
        "ok": False, "error": {
            "code": "unexpected_private_code", "message": (
                "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER " + PRIVATE
            ),
        },
    })
    assert unknown.code == "adept_error"
    assert "autre identité" not in str(unknown)


def test_non_acsm_error_code_preserves_legacy_contract(monkeypatch):
    error = run_failure(monkeypatch, {
        "ok": False,
        "error": {"code": "profile_missing", "message": "profil absent", "retryable": True},
    }, exit_code=1)
    assert error.code == "profile_missing"
    assert error.retryable is True
