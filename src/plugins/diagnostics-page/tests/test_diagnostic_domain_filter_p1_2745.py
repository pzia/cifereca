"""#2745: global, provenance-scoped Browser/Recon filter and GTK controls."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagnostics_explorer import (
    fetch_explorer_page, normalize_diagnostic_domain, sanitized_rows,
)
from diagnostics_selection import collect_filtered_selection


class Broker:
    def __init__(self, rows, *, ignore_domain=False):
        self.rows = rows
        self.calls = []
        self.ignore_domain = ignore_domain

    def events(self, **filters):
        self.calls.append(filters)
        rows = self.rows
        if filters.get("domain") and not self.ignore_domain:
            rows = [row for row in rows if row.get("domain") == filters["domain"]
                    and row.get("source") == "core_events"]
        if filters.get("source") != "all":
            rows = [row for row in rows if row["source"] == filters["source"]]
        if filters.get("severity"):
            rows = [row for row in rows if row["severity"] == filters["severity"]]
        offset, limit = filters["offset"], filters["limit"]
        chunk = rows[offset:offset + limit]
        return {
            "events": chunk, "total": len(rows), "offset": offset,
            "next_offset": offset + len(chunk) if offset + len(chunk) < len(rows) else None,
            "sources": [{"source": "core_events", "status": "available"}],
            "status": "available",
        }


def event(number, *, domain="", source="core_events", module="PLUGIN"):
    record = {
        "timestamp": "2026-10-21T12:00:00+00:00",
        "event_id": f"event-{number:04}",
        "source": source, "module": module, "severity": "error",
        "message": "opération contrôlée", "status": "error",
    }
    if domain:
        record["domain"] = domain
    return record


@pytest.mark.parametrize(("term", "expected"), [
    ("", ""), ("  ", ""),
    ("Browser", "browser_recorder"), ("recorder", "browser_recorder"),
    ("BROWSER-RECORDER", "browser_recorder"),
    ("browser_recorder", "browser_recorder"),
    ("Réconciliation", "reconciliation"), ("RECONCILIATION", "reconciliation"),
    ("Réconcilitation", "reconciliation"),
    ("reconciliation", "reconciliation"),
])
def test_closed_ui_aliases_are_exact_and_accent_aware(term, expected):
    assert normalize_diagnostic_domain(term) == expected


@pytest.mark.parametrize("invalid", [
    True, None, [], {}, "../secret", "PRIVATE-ACCOUNT",
    "browser custom library", "reconcilia", "x" * 65,
])
def test_unknown_domain_does_not_silently_remove_server_filter(invalid):
    broker = Broker([])
    with pytest.raises(ValueError, match="unknown diagnostic domain"):
        fetch_explorer_page(broker, domain=invalid)
    assert broker.calls == []


def test_default_legacy_broker_gets_no_domain_keyword():
    broker = Broker([event(1)])
    page = fetch_explorer_page(broker, period="all")
    assert page["total"] == 1
    assert "domain" not in broker.calls[0]


def test_domain_is_global_before_50_row_pagination_and_combines_severity():
    noise = [event(index) for index in range(117)]
    wanted = [event(200, domain="browser_recorder"),
              event(201, domain="browser_recorder")]
    broker = Broker(noise + wanted)
    page = fetch_explorer_page(
        broker, domain="recorder", period="all", severity="error",
    )
    assert page["total"] == 2
    assert len(page["events"]) == 2
    assert broker.calls[0]["domain"] == "browser_recorder"
    assert broker.calls[0]["severity"] == "error"
    assert {r["event_id"] for r in sanitized_rows(page)} == {
        "event-0200", "event-0201",
    }


def test_bulk_selection_requires_domain_every_page_and_recheck():
    rows = [event(i) for i in range(115)] + [
        event(300, domain="reconciliation", module="LIBRARY"),
        event(301, domain="reconciliation", module="LIBRARY"),
    ]
    broker = Broker(rows)
    result = collect_filtered_selection(
        broker, domain="Réconcilitation", period="all", mode="all",
    )
    assert result.count == 2
    assert result.inspected == 2
    assert [call["offset"] for call in broker.calls] == [0, 0]
    assert all(call["domain"] == "reconciliation" for call in broker.calls)
    data = json.loads(result.export_json)
    assert data["selection"]["domain_filter"] == "reconciliation"
    assert {r["event_id"] for r in data["events"]} == {
        "event-0300", "event-0301",
    }
    assert all("domain" not in row for row in data["events"])


def test_bulk_refuses_broker_ignoring_domain_instead_of_exporting_noise():
    broker = Broker([event(1), event(2, domain="browser_recorder")],
                    ignore_domain=True)
    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(broker, domain="Browser",
                                   period="all", mode="all")


def test_malformed_broker_cannot_fake_domain_via_wrong_category():
    rows = sanitized_rows({"events": [
        event(1, domain="browser_recorder", module="LIBRARY"),
        event(2, domain="reconciliation", module="PLUGIN"),
        event(3, domain="browser_recorder", source="app_log"),
    ]})
    assert all("domain" not in row for row in rows)


def test_gtk_domain_ui_is_separate_and_affects_query_and_selection():
    code = (Path(__file__).resolve().parents[1] /
            "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    assert 'domain_filter = Gtk.Entry()' in code
    assert 'journals_tab.append(domain_row)' in code
    assert 'domain_filter.connect("activate", reset_events)' in code
    assert code.count('"domain": domain_filter.get_text().strip(),') == 2
    assert 'domain_filter.connect("changed", lambda *_: invalidate_selection())' in code
    assert "normalize_diagnostic_domain(domain_filter.get_text())" in code
    assert 'domain=domain_filter.get_text().strip(),' in code
