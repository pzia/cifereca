"""P0 #1120 — read-only evidence for invalid Kobo-ADEPT JSON envelopes.

No CLI subprocess, provider, stdout, account, profile, ACSM or loan is used.
These exact closed causes explain a failed response, not a reason to retry it.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_adept import (
    from_runtime, public_fields, public_log_fields, log_message,
)
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics

PRIVATE = "https://private.invalid/fulfilled.acsm?token=ACCOUNT_SECRET"

CAUSES = (
    "json_not_object",
    "ok_missing",
    "ok_not_boolean",
    "success_with_error",
    "exit_nonzero_with_success",
)


@pytest.mark.parametrize("reason", CAUSES)
def test_invalid_response_cause_survives_closed_core_export_and_log(reason):
    payload = {
        "operation": "loans list", "error_code": "kobo_adept_invalid_response",
        "retryable": False, "returncode": 0 if reason != "exit_nonzero_with_success" else 1,
        "stdout_bytes": 6035, "stderr_bytes": 0,
        "validation_reason": reason,
        "stdout_summary": PRIVATE, "error": {"url": PRIVATE},
        "profile": PRIVATE, "account": PRIVATE, "task_ref": "cccccccccccc",
    }
    fields = from_runtime(payload, "error")
    assert fields["operation"] == "loans list"
    assert fields["code"] == "kobo_adept_invalid_response"
    assert fields["validation_reason"] == reason
    assert fields["retryable"] is False
    assert fields["stdout_bytes"] == 6035
    assert "task_ref" not in fields
    assert public_log_fields(log_message(fields)) == fields
    assert PRIVATE not in repr(fields) + log_message(fields)
    assert "ACCOUNT_SECRET" not in repr(fields)
    assert set(fields) == {
        "schema_version", "plugin_id", "operation_kind", "operation",
        "phase", "status", "code", "retryable", "returncode",
        "stdout_bytes", "stderr_bytes", "validation_reason",
    }


@pytest.mark.parametrize("bad", [
    "ACCOUNT_SECRET", True, False, 1, 0, ["ok_missing"],
    {"source": PRIVATE}, None, object(),
])
def test_hostile_reason_is_never_stringified_or_exported(bad):
    class Hostile:
        def __str__(self):
            raise AssertionError("no coercion of private reason")
        def __repr__(self):
            raise AssertionError("no representation of private reason")

    if bad is None:
        bad = Hostile()
    fields = from_runtime({
        "operation": "loans list",
        "error_code": "kobo_adept_invalid_response",
        "retryable": False,
        "validation_reason": bad,
    }, "error")
    assert fields["code"] == "kobo_adept_invalid_response"
    assert "validation_reason" not in fields
    assert PRIVATE not in json.dumps(fields)


@pytest.mark.parametrize("code,status", [
    ("kobo_adept_timeout", "error"),
    ("adept_error", "error"),
    ("kobo_adept_invalid_response", "ok"),
    ("PRIVATE_UPSTREAM", "error"),
])
def test_reason_never_appears_for_other_error_or_success(code, status):
    fields = from_runtime({
        "operation": "loans list",
        "error_code": code,
        "validation_reason": "ok_missing",
    }, status)
    assert "validation_reason" not in fields
    assert fields["code"] == ("completed" if status == "ok" else (
        code if code != "PRIVATE_UPSTREAM" else "code_non_disponible"
    ))


@pytest.mark.parametrize("corruption", [
    {"validation_reason": "ACCOUNT_SECRET"},
    {"validation_reason": False},
    {"validation_reason": 1},
    {"validation_reason": ["ok_missing"]},
    {"status": "success", "code": "completed"},
    {"code": "kobo_adept_timeout"},
])
def test_public_log_rejects_malformed_or_conflicting_attestation(corruption):
    row = from_runtime({
        "operation": "loans list",
        "error_code": "kobo_adept_invalid_response",
        "validation_reason": "ok_missing",
        "retryable": False,
    }, "error")
    row.update(corruption)
    assert public_fields(row) == {}
    assert public_log_fields(log_message(row)) == {}


def test_verified_core_producer_emits_validation_reason_without_raw_vendor_bytes(tmp_path):
    diag = Diagnostics()
    diag.add_core_kobo_adept({
        "operation": "loans list",
        "error_code": "kobo_adept_invalid_response",
        "validation_reason": "ok_not_boolean",
        "retryable": False,
        "returncode": 0,
        "stdout_bytes": 716,
        "stderr_bytes": 0,
        "stdout_summary": PRIVATE,
        "user": PRIVATE,
    }, "error")
    result = query_diagnostic_events(
        diag, log_path=tmp_path / "not-a-file", source="core_events",
    )
    assert result["total"] == 1
    row, = result["events"]
    assert row["module"] == "ADEPT"
    assert row["detail"]["validation_reason"] == "ok_not_boolean"
    assert row["detail"]["retryable"] is False
    assert "task_ref" not in row
    assert PRIVATE not in json.dumps(result)
    assert "ACCOUNT_SECRET" not in diag.text("test")
