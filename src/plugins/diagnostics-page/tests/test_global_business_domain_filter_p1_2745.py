"""#2745: global domain feed, all-page selection and GTK wiring contracts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagnostics_explorer import fetch_explorer_page
from diagnostics_selection import collect_filtered_selection


PRIVATE = "PRIVATE_READER_TOKEN/path/book.epub"


def _event(i, domain="", *, source="core_events", severity="info"):
    return {
        "source": source,
        "module": "PLUGIN" if domain != "reconciliation" else "LIBRARY",
        "severity": severity, "status": "ok",
        "timestamp": "2026-10-23T11:14:00+00:00",
        "event_id": f"fixture-{i}",
        "message": "opération terminée" if domain else "observation diverse",
        **({"domain": domain} if domain else {}),
    }


class Broker:
    def __init__(self, rows, *, ignore_domain=False):
        self.rows, self.calls = list(rows), []
        self.ignore_domain = ignore_domain

    def events(self, **kwargs):
        self.calls.append(kwargs.copy())
        rows = list(self.rows)
        if kwargs.get("domain") and not self.ignore_domain:
            rows = [row for row in rows
                    if row.get("source") == "core_events"
                    and row.get("domain") == kwargs["domain"]]
        if kwargs.get("severity"):
            rows = [row for row in rows
                    if row["severity"] == kwargs["severity"]]
        offset, limit = kwargs["offset"], kwargs["limit"]
        page = rows[offset:offset + limit]
        return {
            "events": page, "total": len(rows), "offset": offset,
            "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
            "sources": [{"source": "core_events", "status": "available"}],
        }


def test_domain_filters_before_first_page_not_only_local_50_rows():
    broker = Broker(
        [_event(i) for i in range(137)]
        + [_event(i, "browser_recorder") for i in range(137, 141)],
    )
    page = fetch_explorer_page(
        broker, domain="browser_recorder", period="all", source="all",
    )
    assert page["total"] == 4
    assert {row["domain"] for row in page["events"]} == {"browser_recorder"}
    assert broker.calls[0]["domain"] == "browser_recorder"
    assert broker.calls[0]["limit"] == 50


def test_unfiltered_old_core_does_not_receive_new_keyword():
    broker = Broker([_event(1)])
    fetch_explorer_page(broker, period="all")
    assert "domain" not in broker.calls[0]


@pytest.mark.parametrize("bad", [True, None, [], "Browser Recorder", "PLUGIN",
                                 "private.mail@example.org"])
def test_unknown_domain_is_rejected_before_broker_io(bad):
    broker = Broker([])
    with pytest.raises(ValueError, match="invalid diagnostics domain"):
        fetch_explorer_page(broker, domain=bad, period="all")
    assert broker.calls == []


def test_bulk_selection_passes_domain_on_all_pages_and_recheck():
    # The total filtered set exceeds the 50-row page, while >100 unrelated
    # events would otherwise dominate a normal unfiltered first page.
    broker = Broker(
        [_event(i) for i in range(110)]
        + [_event(i, "browser_recorder") for i in range(110, 164)],
    )
    result = collect_filtered_selection(
        broker, source="all", domain="browser_recorder", period="all",
        mode="all",
    )
    assert result.count == result.inspected == 54
    assert [row["offset"] for row in broker.calls] == [0, 50, 0]
    assert all(row["domain"] == "browser_recorder" for row in broker.calls)
    exported = json.loads(result.export_json)
    assert exported["selection"]["domain_filter"] == "browser_recorder"
    assert len(exported["events"]) == 54
    assert PRIVATE not in result.export_json


def test_bulk_selection_rejects_a_server_that_ignores_domain():
    broker = Broker(
        [_event(0), _event(1, "browser_recorder")],
        ignore_domain=True,
    )
    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(
            broker, source="all", domain="browser_recorder", period="all",
            mode="all",
        )


def test_ui_wires_domain_before_paging_and_bulk_export():
    src = (Path(__file__).resolve().parents[1]
           / "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    assert 'domain_selector = Gtk.DropDown.new_from_strings(' in src
    assert 'journals_tab.append(domain_row)' in src
    assert 'domain_selector.connect("notify::selected", reset_events)' in src
    assert src.count('"domain": DOMAINS[int(domain_selector.get_selected())]') == 2
    assert "domain_selector.set_tooltip_text(" in src
    assert "Domaine métier (toutes les pages)" in src


class FakeModel:
    def available(self):
        return True

    def destination(self):
        return {
            "available": True, "remote": False, "host": "localhost",
            "model": "synthetic", "provider": "openai-compatible",
        }


def test_ai_preview_uses_same_domain_scope_without_sending_to_model():
    from diagnostics_ai import prepare_analysis

    broker = Broker([_event(1, "browser_recorder")])
    preview = prepare_analysis(
        FakeModel(), broker, source="core_events",
        domain="browser_recorder",
    )
    assert broker.calls[0]["domain"] == "browser_recorder"
    assert preview["scope"]["domain"] == "browser_recorder"
    assert PRIVATE not in json.dumps(preview)


def test_ai_preview_rejects_broker_ignoring_selected_domain():
    from diagnostics_ai import prepare_analysis

    broker = Broker([_event(0)], ignore_domain=True)
    with pytest.raises(ValueError, match="diagnostics_domain_scope_mismatch"):
        prepare_analysis(
            FakeModel(), broker, source="all", domain="browser_recorder",
        )


def test_ai_old_core_query_unchanged_without_explicit_domain():
    from diagnostics_ai import prepare_analysis

    broker = Broker([_event(1)])
    prepare_analysis(FakeModel(), broker)
    assert "domain" not in broker.calls[0]


def test_ui_ai_carries_global_domain_through_preview():
    src = (Path(__file__).resolve().parents[1]
           / "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    assert "selected_domain = DOMAINS[int(domain_selector.get_selected())]" in src
    assert "provider_id=selected_provider, domain=selected_domain" in src
