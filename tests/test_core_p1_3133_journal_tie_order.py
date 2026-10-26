"""P1 #3133: canonical journal ties follow physical append order, not a hash.

Synthetic local rotated files only; no provider, network, identity or GTK.
"""
from __future__ import annotations

import json

from cifereca.core.diagnostic_journal import (
    export_raw_journal, query_journal, recent_diagnostics_snapshot,
)


TIME = "2026-10-26 10:00:00,123"


def line(label, *, structured=True):
    if structured:
        payload = json.dumps({
            "schema_version": 1, "category": "LOAN",
            "operation": "stage_loan_token", "status": "failed",
            "message": label,
            "detail": {"provider_id": "bnf-pret", "phase": label},
        })
        text = "DIAG_JSON " + payload
    else:
        text = label
    return f"{TIME} ERROR cifereca [Worker] {text}\n"


def test_same_timestamp_order_is_physical_and_consistent_across_surfaces(tmp_path):
    active = tmp_path / "cifereca.log"
    older = tmp_path / "cifereca.log.1"
    oldest = tmp_path / "cifereca.log.2"
    oldest.write_text(line("01 admitted") + line("02 provider started"), encoding="utf-8")
    older.write_text(line("03 provider failed") + line("04 recorded"), encoding="utf-8")
    active.write_text(line("05 surfaced"), encoding="utf-8")

    expected = [
        "01 admitted", "02 provider started", "03 provider failed",
        "04 recorded", "05 surfaced",
    ]
    all_events = query_journal(active, provider_id="bnf-pret", limit=100)
    assert all_events["total"] == len(expected)
    assert [event["message"] for event in all_events["events"]] == expected
    assert len({event["event_id"] for event in all_events["events"]}) == len(expected)
    assert [event["message"] for event in query_journal(
        active, search="03 provider failed", limit=10,
    )["events"]] == ["03 provider failed"]
    assert [
        event["message"] for event in query_journal(
            active, provider_id="bnf-pret", offset=2, limit=2,
        )["events"]
    ] == expected[2:4]
    assert [row["message"] for row in recent_diagnostics_snapshot(active)] == expected
    raw = json.loads(export_raw_journal(active))
    assert [row["message"] for row in raw["events"]] == expected
    assert len({row["timestamp"] for row in raw["events"]}) == 1


def test_distinct_timestamps_still_sorted_chronologically(tmp_path):
    active = tmp_path / "cifereca.log"
    older = tmp_path / "cifereca.log.1"
    # Wall-time shifts and clock adjustments can make recorded timestamps
    # non-monotonic; chronological sorting should remain the primary key.
    older.write_text(
        "2026-10-26 10:00:02,000 INFO cifereca [Worker] future older\n"
        + line("middle"), encoding="utf-8",
    )
    active.write_text(
        "2026-10-26 09:59:59,999 INFO cifereca [Worker] past newest\n"
        + line("middle latest"), encoding="utf-8",
    )
    result = query_journal(active, limit=100)
    assert [row["message"] for row in result["events"]] == [
        "past newest", "middle", "middle latest", "future older",
    ]
