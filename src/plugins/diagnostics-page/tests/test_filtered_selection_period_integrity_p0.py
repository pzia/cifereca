"""P0 #2140 — multi-page bulk export must enforce its frozen time window.

Synthetic read-only Core broker. No filesystem, raw logs, credentials or
real private events. A response contradicting Core's period filter fails as
a whole, not as an incomplete "all errors" export.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

import diagnostics_selection as selection


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def event(number, *, timestamp="2026-10-18T11:00:00+00:00", severity="error"):
    return {
        "event_id": f"evt-{number}", "source": "journal",
        "module": "cifereca.sync", "severity": severity,
        "timestamp": timestamp,
        "message": "public diagnostic", "status": "failed",
    }


class Broker:
    def __init__(self, events, mutate=None):
        self.events_source = events
        self.calls = []
        self.mutate = mutate

    def events(self, **kwargs):
        self.calls.append(dict(kwargs))
        severity = kwargs["severity"]
        rows = [r for r in self.events_source
                if not severity or r["severity"] == severity]
        offset = kwargs["offset"]
        batch = [dict(r) for r in rows[offset: offset+kwargs["limit"]]]
        payload = {
            "events": batch,
            "total": len(rows),
            "offset": offset,
            "next_offset": offset+len(batch)
            if offset+len(batch) < len(rows) else None,
            "sources": [{"source": "journal", "status": "available"}],
        }
        if self.mutate is not None:
            self.mutate(self, kwargs, payload)
        return payload


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(selection, "datetime", FixedClock)


@pytest.mark.parametrize(("period", "old"), [
    ("hour", "2026-10-18T10:59:59+00:00"),
    ("day", "2026-10-17T11:59:59+00:00"),
    ("week", "2026-10-11T11:59:59+00:00"),
    ("month", "2026-09-18T11:59:59+00:00"),
])
def test_older_than_broker_since_is_never_selected(period, old):
    broker = Broker([event(1, timestamp=old)])
    with pytest.raises(ValueError, match="out-of-period") as raised:
        selection.collect_filtered_selection(broker, period=period, mode="errors")
    assert old not in str(raised.value)
    assert all(call["since"] is not None for call in broker.calls)


@pytest.mark.parametrize("timestamp", [
    "", "not-an-iso-date", "PRIVATE_SESSION", "2026-99-99T12:00:00+00:00",
    "2026-10-18T10:59:00", "2026-10-18T10:59:00+25:00",
    "2026-10-18T10:59:00+00:00\nPRIVATE_SESSION",
])
def test_malformed_or_naive_date_cannot_pass_period_filter(timestamp):
    with pytest.raises(ValueError, match="out-of-period") as raised:
        selection.collect_filtered_selection(
            Broker([event(1, timestamp=timestamp)]),
            period="hour", mode="errors",
        )
    assert "PRIVATE_SESSION" not in str(raised.value)


def test_boundary_inclusive_and_utc_offset_normalized():
    broker = Broker([
        event(1, timestamp="2026-10-18T11:00:00Z"),
        event(2, timestamp="2026-10-18T13:30:00+02:00"),
        event(3, timestamp="2026-10-18T12:00:00+00:00", severity="critical"),
    ])
    selected = selection.collect_filtered_selection(
        broker, period="hour", mode="errors",
    )
    assert selected.count == 3
    assert len(selected.event_ids) == 3
    assert json.loads(selected.export_json)["selection"]["period"] == "hour"
    assert {c["since"] for c in broker.calls} == {"2026-10-18T11:00:00+00:00"}
    assert {c["severity"] for c in broker.calls} == {"critical", "error"}


def test_out_of_period_event_on_second_page_rejects_whole_selection():
    rows = [event(i) for i in range(51)]
    rows[-1] = event(50, timestamp="2026-10-17T10:00:00+00:00")
    broker = Broker(rows)
    with pytest.raises(ValueError, match="out-of-period"):
        selection.collect_filtered_selection(broker, period="day", mode="errors")
    assert [c["offset"] for c in broker.calls] == [0, 50]


def test_recheck_of_same_event_ids_detects_newly_out_of_period_date():
    def mutate(broker, kwargs, payload):
        if len(broker.calls) == 2:
            payload["events"][0]["timestamp"] = "2026-10-17T10:00:00+00:00"
    broker = Broker([event(1)], mutate=mutate)
    with pytest.raises(ValueError, match="out-of-period"):
        selection.collect_filtered_selection(
            broker, period="day", severity="error", mode="all",
        )
    assert len(broker.calls) == 2


def test_period_all_does_not_invent_a_time_window():
    broker = Broker([event(1, timestamp="2022-01-01T00:00:00Z")])
    selected = selection.collect_filtered_selection(broker, period="all")
    assert selected.count == 1
    assert all(c["since"] is None for c in broker.calls)
    assert json.loads(selected.export_json)["selection"]["period"] == "all"


def test_local_search_cannot_hide_out_of_period_server_result():
    broker = Broker([event(1, timestamp="2021-01-01T00:00:00Z")])
    with pytest.raises(ValueError, match="out-of-period"):
        selection.collect_filtered_selection(
            broker, period="hour", search="no-match", mode="errors",
        )


def test_empty_broker_batch_keeps_explicit_period_with_no_false_error():
    broker = Broker([])
    result = selection.collect_filtered_selection(broker, period="month")
    assert result.count == 0
    assert result.inspected == 0
    assert {c["since"] for c in broker.calls} == {"2026-09-18T12:00:00+00:00"}


def test_gtk_has_explicit_safe_error_labels_for_time_and_scope_conflicts():
    from pathlib import Path
    source = (
        Path(__file__).parents[1] / "cifereca_diagnostics_page.py"
    ).read_text()
    assert '"diagnostic selection contains out-of-period events",' in source
    assert '"diagnostic selection contains out-of-scope events",' in source
    assert 'fixed if fixed in safe else "Sélection indisponible' in source
