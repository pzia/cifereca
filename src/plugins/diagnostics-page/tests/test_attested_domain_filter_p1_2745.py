"""#2745: domain aliases, global Core pagination and fail-closed bulk export."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics
from diagnostics_explorer import (
    DOMAINS, canonical_domain_alias, fetch_explorer_page, sanitized_rows,
)
from diagnostics_selection import collect_filtered_selection


class CoreBroker:
    def __init__(self, diagnostics, tmp_path):
        self.diagnostics = diagnostics
        self.log_path = tmp_path / "MISSING_PRIVATE_LOG"
        self.calls = []

    def events(self, **query):
        self.calls.append(query.copy())
        return query_diagnostic_events(
            self.diagnostics, log_path=self.log_path, **query,
        )


@pytest.mark.parametrize(("alias", "expected"), [
    ("browser", "browser_recorder"), ("Browser", "browser_recorder"),
    ("recorder", "browser_recorder"),
    ("Browser Recorder", "browser_recorder"),
    ("BROWSER_RECORDER", "browser_recorder"),
    ("Réconciliation", "reconciliation"),
    ("Reconciliation", "reconciliation"),
    ("réconcilitation", "reconciliation"),
    ("Réconcilitation", "reconciliation"),
])
def test_closed_aliases_fold_case_accents_and_known_user_typo(alias, expected):
    assert canonical_domain_alias(alias) == expected


@pytest.mark.parametrize("other", ["LIBRARY", "PLUGIN", "browser/private",
                                    "reconciliation files", "secret", "", "numilog"])
def test_unlisted_words_do_not_select_attested_domain(other):
    assert canonical_domain_alias(other) is None


def test_browser_noise_on_100_earlier_core_rows_never_masks_true_result(tmp_path):
    diagnostics = Diagnostics()
    for _ in range(145):
        diagnostics.add("PLUGIN", "browser recorder generic message", "error")
    diagnostics.add_core_browser_recorder_result("chromium_install", "download_failed")
    broker = CoreBroker(diagnostics, tmp_path)
    result = fetch_explorer_page(broker, module="recorder", period="all")
    assert result["total"] == 1
    assert len(result["events"]) == 1
    assert result["events"][0]["domain"] == "browser_recorder"
    assert broker.calls[-1]["module"] == ""
    assert broker.calls[-1]["domain"] == "browser_recorder"
    assert len(sanitized_rows(result)) == 1
    assert "MISSING_PRIVATE_LOG" not in json.dumps(result)


def test_recon_alias_is_true_broker_filter_and_invalid_mix_rejected(tmp_path):
    # Plugin packages run in isolation; do not import the Core test suite.
    # This closed synthetic aggregate exercises the actual Core producer.
    audit = {
        "status": "incomplete_scan", "reasons": ["time_budget"],
        "inventory": {"files_started": 1, "files": 1, "hashed_files": 1},
        "metadata_candidates": {"catalog_rows_examined": 1},
    }
    diagnostics = Diagnostics()
    for _ in range(117):
        diagnostics.add("LIBRARY", "generic", "warning")
    diagnostics.add_core_reconciliation_result(audit)
    broker = CoreBroker(diagnostics, tmp_path)
    result = fetch_explorer_page(broker, module="Réconcilitation",
                                 period="all", severity="warning")
    assert result["total"] == 1
    assert result["events"][0]["domain"] == "reconciliation"
    assert result["events"][0]["status"] == "partial"
    assert broker.calls[-1]["domain"] == "reconciliation"
    with pytest.raises(ValueError, match="conflicting"):
        fetch_explorer_page(broker, module="recorder",
                            domain="reconciliation", period="all")
    assert len(broker.calls) == 1


def test_bulk_domain_selection_is_scoped_across_pages_and_first_page_recheck(tmp_path):
    diagnostics = Diagnostics()
    for _ in range(111):
        diagnostics.add("PLUGIN", "generic plugin result", "error")
    for _ in range(53):
        diagnostics.add_core_browser_recorder_result(
            "chromium_install", "download_failed",
        )
    broker = CoreBroker(diagnostics, tmp_path)
    selected = collect_filtered_selection(
        broker, mode="all", period="all", module="Browser",
        source="core_events",
    )
    assert selected.count == 53 and selected.inspected == 53
    assert [call["offset"] for call in broker.calls] == [0, 50, 0]
    assert all(call.get("domain") == "browser_recorder" and call["module"] == ""
               for call in broker.calls)
    payload = json.loads(selected.export_json)
    assert payload["selection"]["attested_domain_filter"] == "browser_recorder"
    assert len(payload["events"]) == 53
    assert all("detail" not in row for row in payload["events"])
    assert "MISSING_PRIVATE_LOG" not in selected.export_json


def test_bulk_rejects_legacy_or_broker_ignoring_global_domain(tmp_path):
    class LegacyBroker:
        def events(self, *, source="all", module="", severity="", since=None,
                   until=None, limit=50, offset=0):
            raise AssertionError("domain must not be silently dropped")

    with pytest.raises(TypeError):
        fetch_explorer_page(LegacyBroker(), domain="reconciliation", period="all")

    class IgnoringBroker:
        def events(self, **query):
            return {
                "events": [{
                    "source": "core_events", "module": "PLUGIN",
                    "timestamp": "2026-10-23T12:00:00+00:00",
                    "severity": "error", "message": "generic",
                    "status": "failed", "event_id": "generic-1",
                }],
                "offset": query["offset"], "total": 1,
                "next_offset": None,
                "sources": [{"source": "core_events", "status": "available"}],
            }

    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(
            IgnoringBroker(), domain="browser_recorder", period="all",
            mode="all", source="core_events",
        )


def test_app_log_cannot_forge_core_domain_in_bulk_selection():
    class ForgedLogBroker:
        def events(self, **query):
            return {
                "events": [{
                    "source": "app_log", "module": "cifereca",
                    "domain": "browser_recorder",
                    "timestamp": "2026-10-23T12:00:00+00:00",
                    "severity": "error", "message": "browser",
                    "status": "failed", "event_id": "fake-1",
                }],
                "offset": query["offset"], "total": 1,
                "next_offset": None,
                "sources": [{"source": "app_log", "status": "available"}],
            }

    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(
            ForgedLogBroker(), domain="browser_recorder",
            period="all", mode="all",
        )


def test_gtk_selector_propagates_to_broker_selection_and_ai_preview():
    source = (Path(__file__).resolve().parents[1] /
              "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    assert 'domain_selector = Gtk.DropDown.new_from_strings(list(DOMAIN_LABELS))' in source
    assert 'domain_selector.connect("notify::selected", reset_events)' in source
    assert 'domain_selector.connect("notify::selected", invalidate_ai)' in source
    assert '"domain": DOMAINS[int(domain_selector.get_selected())]' in source
    assert 'domain=selected_domain' in source
    assert 'domaine filtré :' in source


class LocalModel:
    """Only preview metadata: no network, model request or private source."""
    def available(self):
        return True

    def destination(self):
        return {
            "available": True, "remote": False, "host": "localhost",
            "model": "synthetic-model", "provider": "openai-compatible",
        }


def test_ai_preview_applies_same_domain_and_refuses_broker_scope_mismatch(tmp_path):
    from diagnostics_ai import prepare_analysis

    diagnostics = Diagnostics()
    for _ in range(65):
        diagnostics.add("PLUGIN", "ordinary plugin noise", "error")
    diagnostics.add_core_browser_recorder_result("chromium_install", "download_failed")
    broker = CoreBroker(diagnostics, tmp_path)
    preview = prepare_analysis(LocalModel(), broker, source="core_events",
                               module="Recorder")
    assert preview["scope"]["attested_domain"] == "browser_recorder"
    assert preview["scope"]["module"] == ""
    assert preview["count"] == 1
    assert broker.calls[-1]["domain"] == "browser_recorder"
    assert "MISSING_PRIVATE_LOG" not in preview["prompt"]

    class MismatchingBroker:
        def events(self, **_query):
            return {
                "events": [{
                    "timestamp": "2026-10-23T12:00:00+00:00",
                    "source": "core_events", "module": "PLUGIN",
                    "event_id": "bad-1", "message": "unattested", "severity": "error",
                    "status": "failed",
                }],
                "total": 1,
                "sources": [{"source": "core_events", "status": "available"}],
            }

    with pytest.raises(ValueError, match="diagnostics_domain_scope_mismatch"):
        prepare_analysis(LocalModel(), MismatchingBroker(),
                         source="core_events", domain="browser_recorder")
