"""P0 #2380: all public diagnostic timestamps are UTC instants.

Only the fixed local application log gets local-time interpretation. The
snapshot already stores offset-aware instants. No source-to-source task link
is inferred merely because their timestamps agree.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import time
from types import SimpleNamespace

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events


def _snapshot(*timestamps):
    return SimpleNamespace(
        enabled=True,
        snapshot=lambda: [
            {"timestamp": stamp, "category": "SYNC",
             "operation": "sync source", "status": "ok",
             "detail": {"secret": "DONT_EXPORT"}}
            for stamp in timestamps
        ],
    )


def test_offset_snapshots_publish_utc_and_stable_event_id(tmp_path):
    plus = query_diagnostic_events(
        _snapshot("2026-10-20T13:54:51.293+02:00"),
        log_path=tmp_path / "missing", source="core_events",
    )
    utc = query_diagnostic_events(
        _snapshot("2026-10-20T11:54:51.293Z"),
        log_path=tmp_path / "missing", source="core_events",
    )
    expected = "2026-10-20T11:54:51.293000+00:00"
    assert plus["events"][0]["timestamp"] == expected
    assert utc["events"][0]["timestamp"] == expected
    assert plus["events"][0]["event_id"] == utc["events"][0]["event_id"]
    assert plus["sources"][0]["last_event"] == expected
    assert "DONT_EXPORT" not in str(plus)


def test_snapshot_order_and_filters_compare_instants_not_lexical_offsets(tmp_path):
    out = query_diagnostic_events(
        _snapshot("2026-10-20T23:20:00+13:00",
                  "2026-10-20T08:55:00-04:00"),
        log_path=tmp_path / "missing", source="core_events",
        since="2026-10-20T10:00:00Z",
        until="2026-10-20T13:00:00Z",
    )
    assert [row["timestamp"] for row in out["events"]] == [
        "2026-10-20T10:20:00+00:00",
        "2026-10-20T12:55:00+00:00",
    ]
    assert out["sources"][0]["last_event"] == "2026-10-20T12:55:00+00:00"


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="POSIX timezone required")
def test_legacy_local_log_publishes_utc_and_does_not_invent_task_ref(
    tmp_path, monkeypatch,
):
    original_timezone = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    try:
        log = tmp_path / "cifereca.log"
        log.write_text(
            "2026-10-20 13:54:51,293 INFO cifereca.sync [Worker] "
            "TRACE sync source status=partial token=PRIVATE_TOKEN\\n",
            encoding="utf-8",
        )
        out = query_diagnostic_events(
            _snapshot("2026-10-20T11:54:51.293Z"), log_path=log,
            since="2026-10-20T11:54:51Z",
            until="2026-10-20T11:54:52Z",
        )
        assert len(out["events"]) == 2
        assert all(
            datetime.fromisoformat(row["timestamp"]).utcoffset().total_seconds() == 0
            for row in out["events"]
        )
        assert {row["timestamp"] for row in out["events"]} == {
            "2026-10-20T11:54:51.293000+00:00"
        }
        assert all("task_ref" not in row for row in out["events"])
        assert all("PRIVATE_TOKEN" not in str(row) for row in out["events"])
        assert out["sources"][1]["last_event"] == "2026-10-20T11:54:51.293000+00:00"
    finally:
        if original_timezone is None:
            monkeypatch.delenv("TZ")
        else:
            monkeypatch.setenv("TZ", original_timezone)
        time.tzset()
