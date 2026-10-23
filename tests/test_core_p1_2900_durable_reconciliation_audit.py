"""P1 #2900 — durable, bounded private-safe audit evidence after restart."""
from __future__ import annotations

import json
import logging

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_reconciliation import (
    from_audit_result, public_fields,
    public_reconciliation_log_fields, public_reconciliation_log_message,
)
from cifereca.diagnostics import Diagnostics

from tests.test_core_p0_2730_reconciliation_diagnostics import _audit


def _log(tmp_path, message, *, level="WARNING"):
    path = tmp_path / "cifereca.log"
    path.write_text(
        "2026-10-23 10:11:12,000 " + level +
        " cifereca [MainThread] " + message + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(("result", "expected_level"), [
    (_audit("incomplete_scan", ["structure_limit_or_invalid"]), "WARNING"),
    (_audit("incomplete_scan", ["snapshot_unavailable"]), "ERROR"),
    (_audit("incomplete_scan", ["audit_cancelled"]), "WARNING"),
    (_audit(), "INFO"),
])
def test_audit_writes_only_closed_result_and_can_be_read_after_restart(
    caplog, tmp_path, result, expected_level,
):
    diag = Diagnostics()
    with caplog.at_level(logging.INFO, logger="cifereca"):
        diag.add_core_reconciliation_result(result, duration_ms=1234)
    lines = [
        rec for rec in caplog.records
        if rec.getMessage().startswith("EVENT reconciliation audit ")
    ]
    assert len(lines) == 1
    assert lines[0].levelname == expected_level
    body = lines[0].getMessage()
    fields = public_reconciliation_log_fields(body)
    assert fields == public_fields(from_audit_result(result, 1234))
    assert fields["phase"] == "finished"
    assert fields["duration_ms"] == 1234
    assert fields["counts"]["files_observed"] >= 0
    assert "provider_id" not in fields  # one run can span many providers
    file = _log(tmp_path, body, level=expected_level)
    # The process has restarted: no in-memory audit survives.
    feed = query_diagnostic_events(
        Diagnostics(), source="app_log", log_path=file, module="LIBRARY",
    )
    assert feed["total"] == 1
    row = feed["events"][0]
    assert row["source"] == "app_log"
    assert row["module"] == "cifereca"
    assert row["business_module"] == "LIBRARY"
    assert row["detail"] == fields
    assert row["status"] == fields["status"]
    assert "provider_id" not in row
    assert "PRIVATE" not in json.dumps(feed)


def test_core_and_log_are_independent_not_guessed_from_their_timestamps(tmp_path):
    diag = Diagnostics()
    diag.add_core_reconciliation_result(_audit(), duration_ms=11)
    fields = public_fields(from_audit_result(_audit(), 11))
    path = _log(tmp_path, public_reconciliation_log_message(fields), level="INFO")
    result = query_diagnostic_events(diag, log_path=path, module="LIBRARY")
    assert result["total"] == 2
    assert {row["source"] for row in result["events"]} == {"app_log", "core_events"}


@pytest.mark.parametrize("mutate", [
    lambda body: body + " account_id=PRIVATE",
    lambda body: body.replace('"status":"partial"', '"status":"complete"'),
    lambda body: body.replace('"phase":"finished"', '"phase":"PRIVATE"'),
    lambda body: body.replace('"duration_ms":1234', '"duration_ms":"PRIVATE"'),
    lambda body: body.replace('"code":', '"account_id":"PRIVATE","code":'),
    lambda body: body.replace(',"reasons":', ', "reasons":'),
])
def test_untrusted_augmented_log_never_gains_structured_audit_provenance(
    tmp_path, mutate,
):
    fields = public_fields(from_audit_result(
        _audit("incomplete_scan", ["time_budget"]), 1234,
    ))
    base = public_reconciliation_log_message(fields)
    raw = mutate(base)
    assert public_reconciliation_log_fields(raw) == {}
    feed = query_diagnostic_events(
        Diagnostics(), source="app_log", log_path=_log(tmp_path, raw),
    )
    assert feed["total"] == 1
    assert feed["events"][0]["message"].startswith("[contenu libre masqué")
    assert "detail" not in feed["events"][0]
    assert "PRIVATE" not in json.dumps(feed)


def test_inconsistent_severity_never_publishes_claimed_audit(tmp_path):
    fields = public_fields(from_audit_result(_audit(), 40))
    body = public_reconciliation_log_message(fields)
    feed = query_diagnostic_events(
        Diagnostics(), source="app_log", log_path=_log(tmp_path, body, level="ERROR"),
    )
    assert feed["total"] == 1
    assert "detail" not in feed["events"][0]
    assert "business_module" not in feed["events"][0]


def test_unknown_diagnostic_values_cannot_inject_email_title_or_path():
    hostile = _audit("incomplete_scan", ["time_budget"])
    hostile["reasons"] = ["time_budget", "reader@example.test"]
    hostile["inventory"]["phase_ms"]["sha256"] = "PRIVATE"
    safe = public_fields(from_audit_result(hostile))
    body = public_reconciliation_log_message(safe)
    assert "unclassified_limit" in body
    assert "reader@example.test" not in body
    assert "PRIVATE" not in body
    assert "/home" not in body


def test_error_only_and_disabled_modes_respect_audit_control(caplog):
    errors = Diagnostics(level="errors")
    with caplog.at_level(logging.INFO, logger="cifereca"):
        errors.add_core_reconciliation_result(_audit())
        assert not any(
            r.getMessage().startswith("EVENT reconciliation audit ")
            for r in caplog.records
        )
        errors.add_core_reconciliation_result(_audit("incomplete_scan", ["time_budget"]))
    assert any(
        r.getMessage().startswith("EVENT reconciliation audit ")
        for r in caplog.records
    )
    off = Diagnostics(enabled=False)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="cifereca"):
        off.add_core_reconciliation_result(_audit())
    assert not caplog.records
