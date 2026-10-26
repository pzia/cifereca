"""#3133: corrupt DIAG_JSON cannot silently certify a complete diagnostic journal.

Read-only physical fixtures. Raw bytes/URLs, including diagnostic error text,
are not masked. No real provider, UI, credential or network access.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_journal import (
    export_raw_journal, journal_text_export, query_journal,
)

_PREFIX = "2026-10-26 11:00:00,123 ERROR cifereca [MainThread] "


@pytest.mark.parametrize("broken", (
    "DIAG_JSON {broken",
    "DIAG_JSON [1,2,3]",
    'DIAG_JSON {"schema_version": 2, "message": "future schema"}',
    'DIAG_JSON {"schema_version": true, "message": "boolean schema"}',
    'DIAG_JSON {"schema_version": 1.0, "message": "float schema"}',
    "DIAG_JSON " + "[" * 1200 + "0" + "]" * 1200,
))
def test_unreadable_structured_observation_keeps_raw_line_and_marks_partial(tmp_path, broken):
    path = tmp_path / "cifereca.log"
    path.write_text(_PREFIX + broken + "\n", encoding="utf-8")
    result = query_journal(path, limit=50)
    assert result["status"] == "partial"
    assert result["sources"][0]["reason"] == "rotation_or_read_gap"
    assert result["total"] == 1
    event, = result["events"]
    assert event["source"] == "journal"
    assert event["severity"] == "error"
    assert event["message"] == broken
    assert event["status"] == "unknown"
    assert "operation" not in event
    assert "provider_id" not in event
    assert query_journal(path, search="DIAG_JSON")["total"] == 1
    assert query_journal(path, provider_id="bnf-pret")["total"] == 0
    document = json.loads(export_raw_journal(path))
    assert document["coverage"] == "partial"
    assert "structured_event_unreadable" in document["rotation_gaps"]
    assert document["events"][0]["event_id"] == event["event_id"]
    assert document["events"][0]["message"] == broken
    assert "structured_event_unreadable" in journal_text_export(path, version="test")


def test_corrupt_structured_record_does_not_suppress_neighbor_or_new_page(tmp_path):
    valid = {
        "schema_version": 1, "category": "LOAN", "operation": "import_acsm",
        "status": "failed", "message": "BnF HTTP 403",
        "detail": {"provider_id": "bnf-pret", "reason_code": "bnf_http_unavailable"},
    }
    path = tmp_path / "cifereca.log"
    path.write_text(
        _PREFIX + "unstructured plain trace\n"
        + _PREFIX + "DIAG_JSON {\n"
        + _PREFIX + "DIAG_JSON " + json.dumps(valid) + "\n"
        + _PREFIX + "unstructured second trace\n",
        encoding="utf-8",
    )
    full = query_journal(path)
    assert full["status"] == "partial"
    assert full["total"] == 4
    assert len({e["event_id"] for e in full["events"]}) == 4
    filtered = query_journal(path, search="bnf_http_unavailable", limit=1)
    assert filtered["total"] == 1
    assert filtered["events"][0]["operation"] == "import_acsm"
    assert filtered["events"][0]["detail"]["provider_id"] == "bnf-pret"
    assert query_journal(path, provider_id="bnf-pret")["total"] == 1
    assert query_journal(path, search="plain trace")["total"] == 1
    exported = json.loads(export_raw_journal(path))
    assert [e["event_id"] for e in exported["events"]] == [
        e["event_id"] for e in full["events"]
    ]
    assert exported["rotation_gaps"].count("structured_event_unreadable") == 1


def test_valid_structured_event_and_plain_log_keep_complete_coverage(tmp_path):
    path = tmp_path / "cifereca.log"
    path.write_text(
        _PREFIX + 'DIAG_JSON {"schema_version":1,"category":"PNB",'
        '"operation":"download_loan","status":"failed",'
        '"message":"actual exception trace","detail":{"provider_id":"paris"}}\n'
        + _PREFIX + "ordinary warning detail\n",
        encoding="utf-8",
    )
    result = query_journal(path)
    assert result["status"] == "available"
    assert result["total"] == 2
    assert result["events"][0]["operation"] == "download_loan"
    assert result["events"][0]["provider_id"] == "paris"
    assert json.loads(export_raw_journal(path))["rotation_gaps"] == []
