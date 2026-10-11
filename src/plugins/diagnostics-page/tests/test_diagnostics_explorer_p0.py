"""P0 Diagnostics explorer: public broker only, paginated and offline."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from diagnostics_explorer import (
    PAGE_SIZE, SOURCES, SEVERITIES, PERIODS,
    explorer_since, fetch_explorer_page, sanitized_rows, module_groups,
    source_summaries, explorer_header, export_selected_events,
)


class Broker:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload or {
            "events": [], "sources": [
                {"source": "core_events", "status": "empty"},
                {"source": "app_log", "status": "unavailable"},
            ],
            "total": 0, "offset": 0, "next_offset": None,
        }

    def events(self, **filters):
        self.calls.append(filters)
        return self.payload


def event(event_id, *, module="cifereca.sync", source="app_log",
          severity="info", message="TRACE sync source status=ok"):
    return {
        "timestamp": "2026-10-11T07:00:00+02:00", "event_id": event_id,
        "module": module, "source": source, "severity": severity, "message": message,
    }


def test_utc_periods_are_timezone_aware_and_all_is_unbounded():
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    assert explorer_since("all", now=now) is None
    assert explorer_since("hour", now=now) == "2026-10-11T11:00:00+00:00"
    assert explorer_since("day", now=now) == "2026-10-10T12:00:00+00:00"
    assert explorer_since("week", now=now) == "2026-10-04T12:00:00+00:00"
    assert explorer_since("month", now=now) == "2026-09-11T12:00:00+00:00"
    with pytest.raises(ValueError):
        explorer_since("unknown", now=now)


def test_only_public_broker_called_with_exact_bounded_filters():
    broker = Broker()
    response = fetch_explorer_page(
        broker, source="app_log", module="cifereca.sync",
        severity="warning", period="day", offset=100,
    )
    assert response == broker.payload
    assert len(broker.calls) == 1
    args = broker.calls[0]
    assert args["source"] == "app_log"
    assert args["module"] == "cifereca.sync"
    assert args["severity"] == "warning"
    assert args["limit"] == PAGE_SIZE == 50
    assert args["offset"] == 100
    assert args["until"] is None
    assert args["since"].endswith("+00:00")
    assert "path" not in args
    assert "credentials" not in args


@pytest.mark.parametrize("filters", [
    {"source": "local_file"}, {"severity": "security"},
    {"offset": -1}, {"offset": 10001}, {"offset": True},
    {"period": "forever"},
])
def test_invalid_filter_fails_before_broker_access(filters):
    broker = Broker()
    with pytest.raises(ValueError):
        fetch_explorer_page(broker, **filters)
    assert broker.calls == []


def test_search_and_sort_are_current_page_only_and_stable():
    payload = {"events": [
        event("a", message="acquisition ok", severity="info"),
        event("b", message="return failure", severity="error"),
        event("c", message="return confirmed", severity="info"),
    ]}
    newest = sanitized_rows(payload, search="return")
    assert [row["event_id"] for row in newest] == ["c", "b"]
    oldest = sanitized_rows(payload, search="return", newest_first=False)
    assert [row["event_id"] for row in oldest] == ["b", "c"]
    assert "secret" not in oldest[0]
    assert len(sanitized_rows({"events": [event(str(x)) for x in range(90)]})) == 50


def test_module_hierarchy_groups_source_and_severity_and_counts():
    rows = [
        event("1", module="cifereca.sync", severity="info"),
        event("2", module="cifereca.sync", severity="error"),
        event("3", module="cifereca.sync", source="core_events", severity="warning"),
        event("4", module="cifereca.adept", severity="warning"),
    ]
    groups = module_groups(rows)
    assert [(g["module"], g["source"], g["count"]) for g in groups] == [
        ("cifereca.adept", "app_log", 1),
        ("cifereca.sync", "app_log", 2),
        ("cifereca.sync", "core_events", 1),
    ]
    assert groups[1]["severity"] == "error"
    assert groups[2]["severity"] == "warning"


def test_availability_states_are_explicit_even_when_sources_have_no_events():
    states = source_summaries({
        "sources": [
            {"source": "app_log", "status": "partial"},
            {"source": "core_events", "status": "disabled"},
        ],
        "events": [], "offset": 0, "total": 0,
    })
    assert [s["label"] for s in states] == [
        "Journal partiel (rotation / troncation)", "Désactivée",
    ]
    assert explorer_header({"sources": [], "total": 0}, []) == (
        "Sources non alimentées — aucune donnée disponible"
    )
    assert "recherche dans cette page uniquement" in explorer_header(
        {"sources": [{"source": "app_log"}], "total": 40, "offset": 50},
        [event("1")], search="return",
    )


def test_export_only_selected_event_ids_pseudonyms_and_public_fields():
    rows = [
        event("first", message="TRACE sync ok"),
        event("second", message="Bearer ABC123 alice@example.com /home/alex"),
    ]
    rows[1]["runtime_stdin"] = "SHOULD_NOT_LEAK"
    exported = export_selected_events(rows, {"second"})
    payload = json.loads(exported)
    assert len(payload["events"]) == 1
    assert payload["events"][0]["event_id"] == "second"
    for sensitive in ("ABC123", "alice@example.com", "/home/alex", "SHOULD_NOT_LEAK"):
        assert sensitive not in exported
    assert "corrélation" in payload["notice"]


def test_no_task_correlation_is_fabricated_from_unrelated_event_ids():
    payload = {"events": [
        event("a", module="cifereca.sync"),
        event("b", module="cifereca.sync"),
    ]}
    rows = sanitized_rows(payload)
    groups = module_groups(rows)
    assert len(groups) == 1
    assert len(groups[0]["events"]) == 2
    assert all("correlation_id" not in row for row in groups[0]["events"])


@pytest.mark.parametrize("module", [None, 123, [], "x" * 91])
def test_invalid_module_rejected_before_broker_call(module):
    broker = Broker()
    with pytest.raises(ValueError):
        fetch_explorer_page(broker, module=module)
    assert broker.calls == []


@pytest.mark.parametrize("total,offset", [
    ("unknown", "none"), (None, None), ([], {}), (float("inf"), float("nan")),
])
def test_malformed_header_counts_do_not_crash(total, offset):
    header = explorer_header({"sources": [{"source": "app_log"}],
                              "total": total, "offset": offset}, [])
    assert "0 événements affichés" in header
    assert "page à partir de" in header
