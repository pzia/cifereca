"""#2730: global exact reason filtering across Core pagination and selection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagnostics_explorer import fetch_explorer_page, sanitized_rows
from diagnostics_selection import collect_filtered_selection


PRIVATE = "PRIVATE_BOOK /home/alice/9780306406157"


def _event(i, *, reasons=("time_budget",), severity="warning"):
    reasons = list(reasons)
    return {
        "source": "core_events", "module": "LIBRARY", "severity": severity,
        "timestamp": "2026-10-21T19:35:00+00:00",
        "event_id": f"reconcile-{i}", "message": "réconciliation locale incomplète",
        "status": "partial",
        "detail": {
            "schema_version": 1, "operation": "library_reconciliation_audit",
            "status": "partial", "phase": "finished",
            "code": sorted(reasons)[0], "reasons": reasons,
            "counts": {"files_started": 321},
            "private_title": PRIVATE,
        },
    }


class Broker:
    def __init__(self, rows, *, ignore_reason=False):
        self.rows, self.calls = rows, []
        self.ignore_reason = ignore_reason

    def events(self, **kwargs):
        self.calls.append(kwargs.copy())
        # Simulate the verified Core's server-side semantics, including
        # filtering BEFORE paging. The hostile mode deliberately violates it.
        rows = list(self.rows)
        if kwargs["severity"]:
            rows = [row for row in rows if row["severity"] == kwargs["severity"]]
        code = kwargs.get("reason_code", "")
        if code and not self.ignore_reason:
            rows = [row for row in rows if code in row["detail"]["reasons"]]
        start, size = kwargs["offset"], kwargs["limit"]
        page = rows[start:start + size]
        return {
            "events": page, "total": len(rows), "offset": start,
            "next_offset": start + len(page) if start + len(page) < len(rows) else None,
            "sources": [{"source": "core_events", "status": "available"}],
        }


def test_global_reason_code_filters_before_page_and_keeps_secondary_reasons():
    rows = [_event(i, reasons=("snapshot_time_budget",)) for i in range(59)]
    rows[55] = _event(55, reasons=("epub_parser_limit_or_invalid", "time_budget"))
    rows[58] = _event(58, reasons=("time_budget",))
    broker = Broker(rows)
    filtered = fetch_explorer_page(
        broker, source="core_events", module="LIBRARY", period="all",
        reason_code="time_budget",
    )
    assert filtered["total"] == 2 and len(filtered["events"]) == 2
    assert broker.calls[0]["reason_code"] == "time_budget"
    assert {row["event_id"] for row in filtered["events"]} == {
        "reconcile-55", "reconcile-58",
    }
    assert len(sanitized_rows(filtered, include_detail=True)) == 2
    assert PRIVATE not in json.dumps(sanitized_rows(filtered, include_detail=True))


def test_blank_filter_retains_old_core_query_contract():
    broker = Broker([_event(1)])
    fetch_explorer_page(broker, period="all")
    assert "reason_code" not in broker.calls[0]


@pytest.mark.parametrize("bad", ["../../secret", True, None, [], "CODE", "x" * 81])
def test_invalid_code_rejected_before_broker_call(bad):
    broker = Broker([])
    with pytest.raises(ValueError, match="reason code"):
        fetch_explorer_page(broker, period="all", reason_code=bad)
    assert broker.calls == []


def test_bulk_selection_propagates_reason_code_to_every_page_and_recheck():
    rows = [_event(i) for i in range(53)] + [
        _event(100, reasons=("snapshot_time_budget",)),
    ]
    broker = Broker(rows)
    selected = collect_filtered_selection(
        broker, mode="all", source="core_events", module="LIBRARY",
        period="all", reason_code="time_budget",
    )
    assert selected.count == 53 and selected.inspected == 53
    assert [call["offset"] for call in broker.calls] == [0, 50, 0]
    assert all(call["reason_code"] == "time_budget" for call in broker.calls)
    exported = json.loads(selected.export_json)
    assert exported["selection"]["reconciliation_reason_code"] == "time_budget"
    assert len(exported["events"]) == 53
    assert PRIVATE not in selected.export_json
    assert all("detail" not in row for row in exported["events"])


def test_bulk_refuses_server_ignoring_requested_reason_filter():
    broker = Broker([
        _event(0, reasons=("snapshot_time_budget",)),
        _event(1, reasons=("time_budget",)),
    ], ignore_reason=True)
    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(
            broker, mode="all", period="all", source="core_events",
            reason_code="time_budget",
        )


def test_gtk_uses_separate_code_row_and_refreshes_server_page():
    src = (Path(__file__).resolve().parents[1] /
           "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    assert 'reason_code_filter = Gtk.Entry()' in src
    assert 'journals_tab.append(reason_row)' in src
    assert 'reason_code_filter.connect("activate", reset_events)' in src
    assert '"reason_code": reason_code_filter.get_text().strip()' in src
    assert "query[\"reason_code\"]" not in src  # passed only through broker
