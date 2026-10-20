"""P0 #2380: one closed Kobo backup decision schema in both local feeds.

The task references and event IDs are intentionally independent: an instant
match does not prove the log line and Diagnostics entry share a task.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_kobo_projection import (
    public_kobo_backup_decision, public_kobo_backup_log,
)
from cifereca.diagnostics import Diagnostics


PRIVATE = "https://private.invalid/fulfillment/URLLink.acsm?token=SUPERSECRET"
DECISION = {
    "status": "partial", "persisted_enabled": True,
    "connected_kobo": 3, "eligible_kobo": 3, "interval_minutes": 60,
    "credential_id": "AN_ACCOUNT", "exception": PRIVATE,
    "file_path": "/home/private/reader.epub",
}
TRACE = (
    "TRACE kobo backup decision status=partial enabled=True"
    " connected=3 eligible=3 interval=60"
)


def _log(path, line):
    path.write_text(
        "2026-10-20 13:54:51,293 INFO cifereca [MainThread] "
        + line + "\n", encoding="utf-8",
    )


def test_kobo_same_bounded_typed_decision_on_both_channels(tmp_path):
    diagnostics = Diagnostics()
    diagnostics.add_core_kobo_backup_decision(DECISION)
    log = tmp_path / "cifereca.log"
    _log(log, TRACE)
    report = query_diagnostic_events(diagnostics, log_path=log, limit=20)
    assert report["total"] == 2
    by_source = {row["source"]: row for row in report["events"]}
    expected = {
        "operation_kind": "kobo_backup", "phase": "decision",
        "status": "partial", "enabled": True, "connected": 3,
        "eligible": 3, "interval_minutes": 60,
    }
    assert by_source["core_events"]["detail"] == expected
    assert by_source["app_log"]["detail"] == expected
    assert by_source["core_events"]["message"] == by_source["app_log"]["message"]
    assert by_source["core_events"]["severity"] == "warning"
    assert by_source["app_log"]["severity"] == "info"  # severity is source-local
    assert by_source["core_events"]["event_id"] != by_source["app_log"]["event_id"]
    assert all("task_ref" not in row for row in report["events"])
    dump = json.dumps(report)
    assert "SUPERSECRET" not in dump and "AN_ACCOUNT" not in dump
    assert "/home/private" not in dump and "private.invalid" not in dump



def test_generic_diagnostics_cannot_attest_well_formed_kobo_decision(tmp_path):
    """An arbitrary producer's matching operation and enums prove no Core act."""
    diagnostics = Diagnostics()
    diagnostics.add(
        "KOBO", "automatic backup decision", "warning",
        detail={**DECISION, "status": "partial"},
    )
    row, = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "missing", source="core_events",
    )["events"]
    assert row["module"] == "KOBO"
    assert row["message"] == "[opération masquée]"
    assert "detail" not in row
    assert "task_ref" not in row
    assert "AN_ACCOUNT" not in json.dumps(row)


def test_core_kobo_producer_rejects_invalid_fields_before_storage(tmp_path):
    diagnostics = Diagnostics()
    diagnostics.add_core_kobo_backup_decision({
        **DECISION, "status": PRIVATE,
    })
    assert diagnostics.snapshot() == []
    diagnostics.add_core_kobo_backup_decision(DECISION)
    row, = diagnostics.snapshot()
    assert row["verified_kobo_backup"] is True
    assert row["detail"] == {
        "status": "partial", "persisted_enabled": True,
        "connected_kobo": 3, "eligible_kobo": 3, "interval_minutes": 60,
    }
    assert "AN_ACCOUNT" not in json.dumps(row)
    assert "SUPERSECRET" not in json.dumps(row)
    public, = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "missing", source="core_events",
    )["events"]
    assert public["detail"]["operation_kind"] == "kobo_backup"
    assert "task_ref" not in public


def test_core_kobo_producer_respects_disabled_and_errors_only_levels():
    for collector in (Diagnostics(enabled=False), Diagnostics(level="errors")):
        collector.add_core_kobo_backup_decision(DECISION)
        assert collector.snapshot() == []


@pytest.mark.parametrize("invalid", [
    {**DECISION, "persisted_enabled": 1},
    {**DECISION, "connected_kobo": True},
    {**DECISION, "eligible_kobo": 100_001},
    {**DECISION, "eligible_kobo": 4},
    {**DECISION, "status": PRIVATE},
    {**DECISION, "interval_minutes": -1},
])
def test_invalid_core_decision_fails_closed_without_raw_fallback(invalid, tmp_path):
    d = Diagnostics()
    d.add("KOBO", "automatic backup decision", "warning", detail=invalid)
    out = query_diagnostic_events(d, log_path=tmp_path / "missing", source="core_events")
    assert "detail" not in out["events"][0]
    assert out["events"][0]["message"] == "[opération masquée]"
    assert PRIVATE not in json.dumps(out)


@pytest.mark.parametrize("attack", [
    TRACE + " credential_id=SUPERSECRET",
    TRACE + " token=SUPERSECRET",
    TRACE.replace("connected=3", "connected=AN_ACCOUNT"),
    TRACE.replace("status=partial", "status=token"),
    TRACE.replace("eligible=3", "eligible=999999"),
    "TRACE kobo backup decision status=partial enabled=True connected=3 eligible=3 interval=60\n" + PRIVATE,
])
def test_unverified_kobo_trace_is_not_promoted_to_technical_fact(tmp_path, attack):
    log = tmp_path / "cifereca.log"
    _log(log, attack.replace("\n", " "))
    result = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert len(result["events"]) == 1
    assert "detail" not in result["events"][0]
    assert "kobo backup decision status=" not in result["events"][0]["message"]
    assert "SUPERSECRET" not in json.dumps(result)
    assert PRIVATE not in json.dumps(result)


def test_other_kobo_events_do_not_inherit_decision_evidence(tmp_path):
    d = Diagnostics()
    d.add("KOBO", "automatic backup task", "warning", detail=DECISION)
    d.add("KOBO", "automatic backup decision", "warning", detail=None)
    report = query_diagnostic_events(d, log_path=tmp_path / "missing", source="core_events")
    assert all("detail" not in event for event in report["events"])
    assert all("task_ref" not in event for event in report["events"])


def test_public_kobo_parser_does_not_coerce_foreign_objects():
    class Hostile:
        def __str__(self):
            raise AssertionError("No stringification of provider objects")
    assert public_kobo_backup_decision({**DECISION, "status": Hostile()}) == {}
    assert public_kobo_backup_log(Hostile()) == {}
