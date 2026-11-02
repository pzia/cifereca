"""#979: preserve installed Kobo-ADEPT 1.3 machine errors across Core channels.

The upstream JSON error.code is already validated by the runtime adapter.
Core must not lose it during a *second* independent closed-code validation.
These tests contain no Adobe profile, account, ACSM, secret or real subprocess.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_adept import (
    ERROR_CODES, from_runtime, log_message, public_fields, public_log_fields,
)
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from tests.test_adept_shared_diagnostics import install_runtime


CONTRACT_13 = (
    "invalid_acsm",
    "profile_not_found",
    "adept_profile_required",
    "adept_profile_error",
    "adept_profile_incompatible",
)
PRIVATE = "PRIVATE_979_URL https://private.invalid/?token=PRIVATE_979_URL"


def event(code, *, retryable=False, operation="download request"):
    return {
        "operation": operation,
        "error_code": code, "retryable": retryable,
        "duration_ms": 21, "returncode": 10,
        "stdout_bytes": 83, "stderr_bytes": 127,
        # None of these opaque vendor details is part of the public schema.
        "stdout_summary": PRIVATE, "stderr_summary": PRIVATE,
        "stderr": PRIVATE, "message": PRIVATE, "technical_detail": PRIVATE,
        "exception": PRIVATE, "profile": PRIVATE, "account": PRIVATE,
    }


@pytest.mark.parametrize("code", CONTRACT_13)
def test_qualified_runtime_contract13_codes_survive_full_public_log_roundtrip(code):
    assert code in ERROR_CODES
    fields = from_runtime(event(code), "error")
    assert fields["code"] == code
    assert fields["status"] == "failed"
    assert fields["operation"] == "download request"
    assert fields["returncode"] == 10
    assert fields["retryable"] is False
    assert public_fields(fields) == fields
    raw = log_message(fields)
    assert public_log_fields(raw) == fields
    assert PRIVATE not in raw
    assert "stdout" not in raw.replace("stdout_bytes", "")
    assert "stderr" not in raw.replace("stderr_bytes", "")
    assert "profile" not in raw.replace("adept_profile_", "").replace("profile_not_found", "")


@pytest.mark.parametrize("code", CONTRACT_13)
def test_installed_runtime_publication_keeps_same_code_without_vendor_payload(
    tmp_path, code,
):
    manager, context, diag = install_runtime(tmp_path)
    try:
        context.report_diagnostic(PRIVATE, "error", detail=event(code))
        rows = query_diagnostic_events(
            diag, source="core_events", log_path=tmp_path / "no-such-log",
            limit=100,
        )["events"]
        observed = [r for r in rows
                    if r.get("detail", {}).get("operation_kind") == "kobo_adept"]
        assert len(observed) == 1
        assert observed[0]["detail"]["code"] == code
        assert observed[0]["detail"]["retryable"] is False
        assert observed[0]["detail"]["returncode"] == 10
        assert PRIVATE not in json.dumps(observed)
        assert PRIVATE not in diag.text("test")
    finally:
        manager.close()


@pytest.mark.parametrize("bad", [
    "E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER",  # raw vendor alias: runtime normalizes
    "adept_profile_incompatible: " + PRIVATE,
    PRIVATE,
    {"code": "invalid_acsm"},
    True,
    None,
])
def test_unqualified_or_hostile_code_never_becomes_adobe_diagnosis(bad):
    fields = from_runtime(event(bad, retryable=True), "error")
    assert fields["code"] == "code_non_disponible"
    assert "retryable" not in fields
    assert PRIVATE not in json.dumps(fields)
    assert public_log_fields(log_message(fields)) == fields


def test_exit_ten_alone_never_infers_a_precise_adobe_reason():
    fields = from_runtime({
        **event("adept_error"), "error_code": None, "returncode": 10,
    }, "error")
    assert fields["code"] == "code_non_disponible"
    assert "retryable" not in fields
    assert fields["returncode"] == 10


@pytest.mark.parametrize("value", [1, "true", None, [], {}])
def test_non_boolean_runtime_retry_metadata_is_not_attested(value):
    fields = from_runtime(event("adept_profile_incompatible", retryable=value), "error")
    assert fields["code"] == "adept_profile_incompatible"
    assert "retryable" not in fields


def test_typed_retry_metadata_remains_evidence_not_an_automatic_retry():
    fields = from_runtime(event("adept_profile_error", retryable=True, operation="profile list"), "error")
    assert fields["retryable"] is True
    assert fields["operation"] == "profile list"
    # No acquisition/POST command or retry is performed by this pure projection.


def test_canonical_public_envelope_rejects_injected_private_fields():
    good = from_runtime(event("invalid_acsm"), "error")
    assert not public_fields({**good, "vendor_response": PRIVATE})
    assert not public_fields({**good, "code": PRIVATE})
    assert not public_fields({**good, "retryable": 1})
    assert not public_fields({**good, "schema_version": 2})
    assert not public_fields({**good, "operation_kind": "another_plugin"})
    assert not public_fields({**good, "status": "success"})


def test_success_cannot_smuggle_a_failure_code_or_nonzero_exit():
    assert from_runtime(event("invalid_acsm"), "ok") == {}
    successful = {**event("invalid_acsm"), "returncode": 0}
    fields = from_runtime(successful, "ok")
    assert fields["code"] == "completed"
    assert "retryable" not in fields
    assert "error_code" not in fields


def test_legacy_structured_already_fulfilled_code_remains_qualified():
    code = "adept_license_already_fulfilled_by_another_user"
    fields = from_runtime(event(code), "error")
    assert fields["code"] == code
    assert fields["retryable"] is False
    assert public_log_fields(log_message(fields)) == fields
