"""#3195: multi-page Diagnostics selection must honor a selected journal provider.

All data is an in-memory synthetic broker feed. No GTK, HTTP or file access.
"""
from __future__ import annotations

import json

import pytest

from diagnostics_selection import collect_filtered_selection


def _event(provider, *, event_id="evt-1"):
    row = {
        "source": "journal", "event_id": event_id,
        "timestamp": "2026-10-26T10:00:00+00:00",
        "module": "LOAN", "severity": "error",
        "status": "failed", "message": "HTTP 403",
    }
    if provider is not None:
        row["provider_id"] = provider
    return row


class OnePageJournal:
    def __init__(self, event):
        self.event = event
        self.calls = []

    def events(self, **query):
        self.calls.append(query)
        return {
            "events": [dict(self.event)], "total": 1,
            "offset": query["offset"], "next_offset": None,
            "sources": [{"source": "journal", "status": "available"}],
        }


@pytest.mark.parametrize("reported", ["paris", None, "provider.other"])
def test_filtered_selection_refuses_journal_row_from_another_provider(reported):
    broker = OnePageJournal(_event(reported))
    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(
            broker, source="journal", provider_id="bnf-pret",
            period="all", severity="error", mode="all",
        )
    assert len(broker.calls) == 1
    assert broker.calls[0]["provider_id"] == "bnf-pret"
    assert broker.calls[0]["source"] == "journal"


def test_exact_journal_provider_remains_selectable_and_copyable():
    broker = OnePageJournal(_event("bnf-pret"))
    result = collect_filtered_selection(
        broker, source="journal", provider_id="bnf-pret",
        period="all", severity="error", mode="all",
    )
    assert result.count == result.inspected == 1
    assert result.event_ids == frozenset({"evt-1"})
    assert len(broker.calls) == 2  # first page + consistency recheck
    content = json.loads(result.export_json)
    assert content["selection"]["provider_filter"] == "bnf-pret"
    assert content["events"][0]["provider_id"] == "bnf-pret"
    assert content["events"][0]["event_id"] == "evt-1"


def test_no_provider_filter_keeps_journal_rows_without_synthetic_attribution():
    broker = OnePageJournal(_event(None))
    result = collect_filtered_selection(
        broker, source="journal", period="all", severity="error", mode="all",
    )
    assert result.count == 1
    content = json.loads(result.export_json)
    assert "provider_id" not in content["events"][0]
    assert content["selection"]["provider_filter"] is None
