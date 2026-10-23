"""#2745: the verified domain is applied globally and survives bulk selection.

These are synthetic, public Core-shape observations. No browser capture,
signed URL, account, filesystem path or user file is opened.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagnostics_explorer import (
    canonical_diagnostic_domain_alias, fetch_explorer_page, sanitized_rows,
)
from diagnostics_selection import collect_filtered_selection


class Broker:
    def __init__(self, rows, *, ignore_domain=False):
        self.rows = rows
        self.calls = []
        self.ignore_domain = ignore_domain

    def events(self, **kw):
        self.calls.append(dict(kw))
        rows = list(self.rows)
        if kw["source"] != "all":
            rows = [row for row in rows if row["source"] == kw["source"]]
        if kw["module"]:
            rows = [row for row in rows
                    if row["module"].casefold().startswith(kw["module"].casefold())]
        if kw["severity"]:
            rows = [row for row in rows if row["severity"] == kw["severity"]]
        if kw.get("domain") and not self.ignore_domain:
            rows = [row for row in rows if row.get("domain") == kw["domain"]]
        start, limit = kw["offset"], kw["limit"]
        page = rows[start:start + limit]
        return {
            "events": page, "total": len(rows), "offset": start,
            "next_offset": start + len(page) if start + len(page) < len(rows) else None,
            "sources": [{"source": "core_events", "status": "available"}],
        }


def event(index, *, domain=""):
    row = {
        "source": "core_events", "module": "PLUGIN", "severity": "error",
        "timestamp": "2026-10-21T09:00:00+00:00",
        "event_id": f"event-{index}", "message": "browser recorder opération",
        "status": "error",
    }
    if domain:
        row["domain"] = domain
    return row


@pytest.mark.parametrize(("alias", "canonical"), [
    ("Browser", "browser_recorder"), ("recorder", "browser_recorder"),
    ("Browser Recorder", "browser_recorder"),
    ("Réconciliation", "reconciliation"),
    ("Reconciliation", "reconciliation"),
    ("Réconcilitation", "reconciliation"),
])
def test_filter_alias_is_canonical_before_broker_call(alias, canonical):
    assert canonical_diagnostic_domain_alias(alias) == canonical
    broker = Broker([])
    fetch_explorer_page(broker, period="all", domain=alias)
    assert broker.calls[0]["domain"] == canonical


def test_domain_filter_works_before_page_50_amid_unrelated_plugin_noise():
    rows = [event(i) for i in range(120)] + [
        event(i, domain="browser_recorder") for i in range(120, 173)
    ]
    broker = Broker(rows)
    page = fetch_explorer_page(broker, period="all", domain="recorder")
    assert page["total"] == 53
    assert len(page["events"]) == 50
    assert page["next_offset"] == 50
    last = fetch_explorer_page(broker, period="all", domain="Browser", offset=50)
    assert last["total"] == 53 and len(last["events"]) == 3
    assert all(row["domain"] == "browser_recorder"
               for row in page["events"] + last["events"])
    # Legacy blank filter does not gain a new broker keyword.
    fetch_explorer_page(broker, period="all")
    assert "domain" not in broker.calls[-1]


def test_bulk_selection_retains_exact_domain_on_all_pages_and_recheck():
    rows = [event(i) for i in range(120)] + [
        event(i, domain="browser_recorder") for i in range(120, 173)
    ]
    broker = Broker(rows)
    result = collect_filtered_selection(
        broker, source="core_events", period="all",
        domain="Browser", mode="all",
    )
    assert result.count == result.inspected == 53
    assert len(broker.calls) == 3
    assert [c["offset"] for c in broker.calls] == [0, 50, 0]
    assert all(c["domain"] == "browser_recorder" for c in broker.calls)
    exported = json.loads(result.export_json)
    assert exported["selection"]["domain_filter"] == "browser_recorder"
    assert len(exported["events"]) == 53
    assert all("domain" not in row and "detail" not in row
               for row in exported["events"])


def test_bulk_rejects_broker_ignoring_requested_domain():
    broker = Broker([event(0), event(1, domain="browser_recorder")],
                    ignore_domain=True)
    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(
            broker, source="core_events", period="all",
            domain="recorder", mode="all",
        )


@pytest.mark.parametrize("bad", [None, True, ["browser"], "PRIVATE/path", "PLUGIN", "x" * 41])
def test_bad_domain_rejected_before_broker_access(bad):
    broker = Broker([])
    with pytest.raises(ValueError, match="diagnostics domain"):
        fetch_explorer_page(broker, domain=bad, period="all")
    assert broker.calls == []


def test_gtk_has_distinct_global_domain_entry_and_does_not_export_private_text():
    src = (Path(__file__).resolve().parents[1] /
           "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    assert "domain_filter = Gtk.Entry()" in src
    assert "domain_filter.connect(\"activate\", reset_events)" in src
    assert '"domain": domain_filter.get_text().strip()' in src
    assert 'journals_tab.append(domain_row)' in src
    assert "canonical_diagnostic_domain_alias" in src
    assert "Rechercher dans cette page" in src
