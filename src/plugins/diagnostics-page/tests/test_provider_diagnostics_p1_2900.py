"""#2900 — provider filtering is global, exact and privacy-safe in Diagnostics UI."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from diagnostics_explorer import (
    fetch_explorer_page as canonical_explorer_page, sanitized_rows, export_selected_events,
)
from diagnostics_selection import collect_filtered_selection as canonical_selection


# #3133: provider compatibility fixtures opt into historical sources.
def fetch_explorer_page(broker, **filters):
    return canonical_explorer_page(broker, source=filters.pop("source", "all"), **filters)


def collect_filtered_selection(broker, **filters):
    return canonical_selection(broker, source=filters.pop("source", "all"), **filters)


def event(idx, provider="paris"):
    return {
        "event_id": f"event-{idx}",
        "timestamp": "2026-10-23T06:00:00+00:00",
        "source": "core_events", "module": "SYNC",
        "severity": "error", "message": "source sync failed",
        "status": "failed", "provider_id": provider,
    }


class Broker:
    def __init__(self, rows, *, ignore_filter=False):
        self.rows = rows
        self.calls = []
        self.ignore_filter = ignore_filter

    def events(self, **filters):
        self.calls.append(dict(filters))
        rows = [
            r for r in self.rows
            if (not filters["severity"] or r["severity"] == filters["severity"])
            and (filters["source"] == "all" or r["source"] == filters["source"])
            and (not filters["module"]
                 or r["module"].casefold().startswith(filters["module"].casefold()))
            and (self.ignore_filter or not filters.get("provider_id")
                 or r.get("provider_id") == filters["provider_id"])
        ]
        start, size = filters["offset"], filters["limit"]
        return {
            "events": rows[start:start + size],
            "total": len(rows), "offset": start,
            "next_offset": start + size if start + size < len(rows) else None,
            "sources": [{"source": "core_events", "status": "available"}],
        }


def test_provider_is_an_exact_server_side_filter_not_module_alias():
    broker = Broker([event(1), event(2, "kobo")])
    payload = fetch_explorer_page(
        broker, period="all", source="core_events", module="SYNC",
        provider_id="paris",
    )
    assert broker.calls[0]["provider_id"] == "paris"
    assert broker.calls[0]["module"] == "SYNC"
    assert len(payload["events"]) == 1
    assert sanitized_rows(payload)[0]["provider_id"] == "paris"
    legacy = Broker([event(1)])
    fetch_explorer_page(legacy, period="all")
    assert "provider_id" not in legacy.calls[0]


@pytest.mark.parametrize("invalid", [
    "reader@example.test", "account.alice", "secret", "/private/path",
    "https://bibliotheques.paris.fr", "paris?q=token", "x" * 90,
    None, 1, True,
])
def test_invalid_provider_never_calls_broker(invalid):
    broker = Broker([])
    with pytest.raises(ValueError, match="provider"):
        fetch_explorer_page(broker, provider_id=invalid)
    assert broker.calls == []


def test_published_provider_is_safe_in_search_clipboard_and_private_values_removed():
    safe = event(1)
    hostile = event(2, "reader@example.test")
    rows = sanitized_rows({"events": [safe, hostile]}, include_detail=True)
    by_id = {row["event_id"]: row for row in rows}
    assert by_id["event-1"]["provider_id"] == "paris"
    assert "provider_id" not in by_id["event-2"]
    assert [r["event_id"] for r in sanitized_rows({"events": [safe, hostile]}, search="paris")] == ["event-1"]
    packed = export_selected_events(rows, {"event-1", "event-2"})
    assert '"provider_id": "paris"' in packed
    assert "reader@example.test" not in packed


def test_bulk_selection_rejects_broker_that_ignores_provider_filter():
    broker = Broker([event(1, "kobo")], ignore_filter=True)
    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(
            broker, period="all", severity="error", mode="all",
            provider_id="paris",
        )
    assert broker.calls[0]["provider_id"] == "paris"


def test_bulk_selection_retains_provider_scope_in_export_without_private_accounts():
    broker = Broker([event(1), event(2, "kobo")])
    selected = collect_filtered_selection(
        broker, period="all", severity="error", mode="all",
        provider_id="paris",
    )
    result = json.loads(selected.export_json)
    assert selected.count == 1
    assert result["selection"]["provider_filter"] == "paris"
    assert result["events"][0]["provider_id"] == "paris"
    assert len(broker.calls) == 2  # first page and exact-scope consistency recheck


def test_ui_has_explicit_filter_and_ai_inherits_same_server_scope():
    root = Path(__file__).resolve().parents[1]
    page = (root / "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    ai = (root / "diagnostics_ai.py").read_text(encoding="utf-8")
    ast.parse(page)
    ast.parse(ai)
    assert 'provider_filter.connect("activate", reset_events)' in page
    assert '"provider_id": provider_filter.get_text().strip()' in page
    assert 'provider_id=selected_provider' in page
    assert 'query["provider_id"] = provider_id' in ai
    assert 'ex. paris' in page


def test_ai_preserves_attested_provider_in_evidence_and_exact_scope_without_sending():
    from diagnostics_ai import prepare_analysis
    from tests.test_diagnostics_ai_p0 import FakeBroker, FakeLLM
    model = FakeLLM()
    broker = FakeBroker([event(1)])
    preview = prepare_analysis(model, broker, provider_id="paris")
    assert broker.filters[0]["provider_id"] == "paris"
    assert preview["scope"]["provider_id"] == "paris"
    # Provider appears both in the requested scope and in the actual event.
    assert preview["prompt"].count('"provider_id": "paris"') == 2
    assert model.calls == 0


@pytest.mark.parametrize("provider", ["kobo", "paris.catalog", None,
                                      "PRIVATE@example.invalid", True])
def test_ai_refuses_broker_ignoring_exact_provider_filter_before_consent(provider):
    from diagnostics_ai import prepare_analysis
    from tests.test_diagnostics_ai_p0 import FakeBroker, FakeLLM
    model = FakeLLM()
    with pytest.raises(ValueError, match="diagnostics_provider_scope_mismatch"):
        prepare_analysis(model, FakeBroker([event(1, provider)]), provider_id="paris")
    assert model.calls == 0


def test_ai_unfiltered_events_keep_only_valid_explicit_provider():
    from diagnostics_ai import prepare_analysis
    from tests.test_diagnostics_ai_p0 import FakeBroker, FakeLLM
    model = FakeLLM()
    rows = [event(1), event(2, "PRIVATE@example.invalid"), event(3)]
    del rows[-1]["provider_id"]
    rows[-1]["detail"] = {"provider_id": "numilog", "password": "PRIVATE"}
    preview = prepare_analysis(model, FakeBroker(rows))
    assert '"provider_id": "paris"' in preview["prompt"]
    assert "PRIVATE" not in preview["prompt"]
    assert "numilog" not in preview["prompt"]
    assert "provider_id" not in preview["scope"]
    assert model.calls == 0


def test_bulk_selection_refuses_provider_changed_during_consistency_recheck():
    class ChangedBroker(Broker):
        def events(self, **filters):
            if self.calls:
                self.rows[0]["provider_id"] = "kobo"
            return super().events(**filters)
    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(ChangedBroker([event(1)], ignore_filter=True),
                                   provider_id="paris", period="all", mode="all")


def test_old_core_supports_unfiltered_page_and_refuses_explicit_provider():
    class OldCore:
        def events(self, *, source, module, severity, since, until, limit, offset):
            return {"events": [], "total": 0, "offset": offset}
    assert fetch_explorer_page(OldCore())["events"] == []
    with pytest.raises(TypeError):
        fetch_explorer_page(OldCore(), provider_id="paris")


def test_real_core_broker_provider_survives_page_bulk_copy_and_ai(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cifereca.brokers import DiagnosticsBrokerImpl
    from cifereca.diagnostics import Diagnostics
    from diagnostics_ai import prepare_analysis
    from tests.test_diagnostics_ai_p0 import FakeLLM
    diag = Diagnostics(level="errors")
    for provider in ("paris", "kobo", "numilog"):
        diag.add_core_source_sync(provider, phase="completed", status="failed", code="source_sync_failed")
    monkeypatch.setattr("cifereca.paths.log_file", lambda: tmp_path / "missing")
    broker = DiagnosticsBrokerImpl(SimpleNamespace(diagnostics=diag))
    for provider in ("paris", "kobo", "numilog"):
        payload = fetch_explorer_page(broker, provider_id=provider, source="core_events", period="all")
        rows = sanitized_rows(payload)
        assert len(rows) == 1 and rows[0]["provider_id"] == provider
        selected = collect_filtered_selection(broker, provider_id=provider, source="core_events", period="all")
        assert json.loads(selected.export_json)["events"][0]["provider_id"] == provider
        preview = prepare_analysis(FakeLLM(), broker, provider_id=provider, source="core_events")
        assert preview["prompt"].count('"provider_id": "' + provider + '"') == 2
