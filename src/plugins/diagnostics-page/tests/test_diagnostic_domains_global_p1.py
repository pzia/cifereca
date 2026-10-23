"""#2745: all attested business domains use Core paging and multi-page scope.

Synthetic public observations only. No real account, provider, log or URL.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagnostics_explorer import DOMAIN_CHOICES, fetch_explorer_page
from diagnostics_selection import collect_filtered_selection

PRIVATE = "PRIVATE_ACCOUNT /home/private/9780306406157"


def event(index, domain=None):
    row = {
        "timestamp": f"2026-10-20T12:{index % 60:02d}:00+00:00",
        "source": "core_events",
        "module": "PLUGIN",
        "severity": "error",
        "message": "opération masquée",
        "status": "error",
        "event_id": f"event-{index:04d}",
    }
    if domain:
        row["domain"] = domain
    return row


class FakeBroker:
    def __init__(self, rows, *, ignore_domain=False):
        self.rows, self.calls = rows, []
        self.ignore_domain = ignore_domain

    def events(self, **query):
        self.calls.append(dict(query))
        rows = list(self.rows)
        if query.get("domain") and not self.ignore_domain:
            rows = [row for row in rows if row.get("domain") == query["domain"]]
        if query.get("severity"):
            rows = [row for row in rows if row["severity"] == query["severity"]]
        if query.get("source") != "all":
            rows = [row for row in rows if row["source"] == query["source"]]
        offset, limit = query["offset"], query["limit"]
        selected = rows[offset:offset+limit]
        return {
            "events": selected, "total": len(rows),
            "offset": offset,
            "next_offset": offset + len(selected)
            if offset + len(selected) < len(rows) else None,
            "sources": [{"source": "core_events", "status": "available", "reason": ""}],
        }


def test_registry_controls_every_gtk_option():
    codes = [code for code, _label in DOMAIN_CHOICES]
    assert codes[0] == ""
    assert len(codes) == len(set(codes))
    assert len(codes) == 9
    assert all(label and "PRIVATE" not in label for _code, label in DOMAIN_CHOICES)


def test_one_global_filter_reaches_later_pages_and_multi_page_selection():
    rows = [event(i) for i in range(125)] + [
        event(125 + i, "reconciliation") for i in range(55)
    ]
    broker = FakeBroker(rows)
    filtered = fetch_explorer_page(
        broker, source="core_events", domain="Réconciliation",
        period="all",
    )
    assert filtered["total"] == 55
    assert len(filtered["events"]) == 50
    assert broker.calls[0]["domain"] == "reconciliation"
    selection = collect_filtered_selection(
        broker, mode="all", source="core_events",
        domain="reconciliation", period="all",
    )
    assert selection.count == 55
    assert selection.inspected == 55
    assert all(call["domain"] == "reconciliation" for call in broker.calls)
    assert [c["offset"] for c in broker.calls] == [0, 0, 50, 0]
    parsed = json.loads(selection.export_json)
    assert parsed["selection"]["domain_filter"] == "reconciliation"
    assert parsed["selection"]["scope"] == "all_accessible_filtered_pages"
    assert len(parsed["events"]) == 55
    assert all("domain" not in row for row in parsed["events"])
    assert PRIVATE not in selection.export_json


def test_old_core_without_new_filter_remains_compatible_when_unselected():
    broker = FakeBroker([event(1)])
    fetch_explorer_page(broker, period="all")
    assert "domain" not in broker.calls[-1]
    chosen = collect_filtered_selection(broker, mode="all", period="all")
    assert chosen.count == 1
    assert all("domain" not in call for call in broker.calls)


def test_domain_filter_fails_closed_if_server_ignores_it():
    broker = FakeBroker(
        [event(1, "kobo_backup"), event(2, "reconciliation")],
        ignore_domain=True,
    )
    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(
            broker, mode="all", domain="reconciliation", period="all",
        )


@pytest.mark.parametrize("bad", [True, None, [], "PRIVATE", "browser/../../token"])
def test_invalid_domain_rejected_before_plugin_broker_query(bad):
    broker = FakeBroker([])
    with pytest.raises(ValueError, match="domain"):
        fetch_explorer_page(broker, period="all", domain=bad)
    assert broker.calls == []


def test_gtk_selector_is_before_pagination_not_page_only_search():
    source = (Path(__file__).resolve().parents[1] /
              "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    assert "domain_selector = Gtk.DropDown.new_from_strings" in source
    assert "journals_tab.append(domain_row)" in source
    assert 'domain_selector.connect("notify::selected", reset_events)' in source
    assert source.count('"domain": DOMAIN_CHOICES[int(domain_selector.get_selected())][0]') >= 2
    assert "domain=DOMAIN_CHOICES[int(domain_selector.get_selected())][0]" in source
    assert "source_selector.connect" in source


def test_ai_domain_preview_uses_same_global_scope_and_one_use_consent():
    from diagnostics_ai import prepare_analysis, analyze_with_consent
    from tests.test_diagnostics_ai_p0 import FakeBroker, FakeLLM

    model = FakeLLM()
    item = event(1, "reconciliation")
    broker = FakeBroker([item])
    preview = prepare_analysis(model, broker, domain="Réconciliation")
    assert broker.filters[0]["domain"] == "reconciliation"
    assert preview["scope"]["domain"] == "reconciliation"
    assert model.calls == 0
    assert "reconciliation" in preview["prompt"]
    analysis = analyze_with_consent(
        model, preview, approval_id=preview["approval_id"],
    )
    assert analysis["ok"] is True and model.calls == 1
    with pytest.raises(ValueError, match="explicit_consent_required"):
        analyze_with_consent(
            model, preview, approval_id=preview["approval_id"],
        )


def test_ai_rejects_nonmatching_domain_from_misbehaving_broker():
    from diagnostics_ai import prepare_analysis
    from tests.test_diagnostics_ai_p0 import FakeBroker, FakeLLM

    model = FakeLLM()
    with pytest.raises(ValueError, match="diagnostics_domain_scope_mismatch"):
        prepare_analysis(
            model, FakeBroker([event(1, "kobo_backup")]), domain="reconciliation",
        )
    assert model.calls == 0
