"""#3133: malformed typed DIAG_JSON fields cannot imply a complete journal.

Synthetic raw log, no Core producer side effects, HTTP or credentials.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_journal import export_raw_journal, query_journal

BASE = {
    "schema_version": 1, "category": "LOAN", "operation": "download_loan",
    "status": "failed", "message": "BNF ACSM HTTP 403",
    "detail": {"provider_id": "bnf-pret", "phase": "provider_delivery"},
    "task_ref": "abcdef123456", "duration_ms": 123,
}
PREFIX = "2026-10-26 10:00:00,123 ERROR cifereca [Worker] DIAG_JSON "


@pytest.mark.parametrize(("field", "value"), (
    ("category", 8), ("operation", False), ("status", None),
    ("message", ["wrong"]), ("detail", ["not a mapping"]),
    ("task_ref", {"ref": "abcdef123456"}),
    ("duration_ms", "123"), ("duration_ms", -1),
    ("duration_ms", 86_400_001), ("duration_ms", True),
))
def test_wrong_typed_present_field_keeps_raw_and_marks_gap(tmp_path, field, value):
    bad = {**BASE, field: value}
    original = "DIAG_JSON " + json.dumps(bad, ensure_ascii=False)
    path = tmp_path / "cifereca.log"
    path.write_text(
        "2026-10-26 10:00:00,123 ERROR cifereca [Worker] "
        + original + "\n", encoding="utf-8",
    )
    state = query_journal(path)
    assert state["total"] == 1
    assert state["status"] == "partial"
    event, = state["events"]
    assert event["message"] == original
    assert event["status"] == "unknown"
    assert "operation" not in event
    assert "detail" not in event and "provider_id" not in event
    assert "task_ref" not in event and "duration_ms" not in event
    assert query_journal(path, provider_id="bnf-pret")["total"] == 0
    raw = json.loads(export_raw_journal(path))
    assert raw["coverage"] == "partial"
    assert raw["rotation_gaps"] == ["structured_event_unreadable"]
    assert raw["events"][0]["message"] == original
    assert raw["events"][0]["event_id"] == event["event_id"]


def test_valid_schema_one_fields_remain_searchable_and_complete(tmp_path):
    path = tmp_path / "cifereca.log"
    path.write_text(
        PREFIX + json.dumps(BASE) + "\n", encoding="utf-8",
    )
    result = query_journal(path, search="provider_delivery")
    assert result["status"] == "available"
    assert result["total"] == 1
    assert result["events"][0]["operation"] == "download_loan"
    assert result["events"][0]["status"] == "failed"
    assert result["events"][0]["provider_id"] == "bnf-pret"
    assert result["events"][0]["task_ref"] == "abcdef123456"
    assert json.loads(export_raw_journal(path))["rotation_gaps"] == []


def test_missing_optional_fields_are_not_equated_with_malformed_values(tmp_path):
    minimal = {"schema_version": 1, "message": "Some observed event"}
    path = tmp_path / "cifereca.log"
    path.write_text(
        PREFIX + json.dumps(minimal) + "\n", encoding="utf-8",
    )
    result = query_journal(path)
    assert result["status"] == "available"
    assert result["events"][0]["message"] == "Some observed event"
    assert result["events"][0]["status"] == "unknown"
    assert "operation" not in result["events"][0]
