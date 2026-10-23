"""#2745: business domain is an attested Core query, not a text search."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from diagnostics_explorer import (
    fetch_explorer_page, public_domain_id, sanitized_rows,
)
from diagnostics_selection import collect_filtered_selection


def _row(index, *, domain=None, module="PLUGIN"):
    result = {
        "event_id": "domain-" + str(index),
        "timestamp": "2026-10-23T08:00:00+00:00",
        "source": "core_events",
        "module": module,
        "message": "bounded synthetic event",
        "severity": "error",
        "status": "error",
    }
    if domain:
        result["domain"] = domain
    return result


class Broker:
    def __init__(self, rows, ignore_filter=False):
        self.rows = rows
        self.calls = []
        self.ignore_filter = ignore_filter

    def events(self, **options):
        self.calls.append(dict(options))
        rows = [
            row for row in self.rows
            if (options["source"] == "all" or row["source"] == options["source"])
            and (not options["severity"] or row["severity"] == options["severity"])
            and (not options["module"]
                 or row["module"].casefold().startswith(options["module"].casefold()))
            and (self.ignore_filter or not options.get("domain")
                 or row.get("domain") == options["domain"])
        ]
        offset, size = options["offset"], options["limit"]
        return {
            "events": rows[offset:offset + size],
            "offset": offset, "total": len(rows),
            "next_offset": offset + size if offset + size < len(rows) else None,
            "sources": [{"source": "core_events", "status": "available"}],
        }


def test_domain_selector_is_server_side_and_reaches_later_pages():
    noise = [_row(i) for i in range(140)]
    broker = Broker(noise + [_row(200, domain="browser_recorder")])
    page = fetch_explorer_page(broker, source="core_events",
                               period="all", domain="Recorder")
    assert page["total"] == 1
    assert broker.calls[0]["domain"] == "browser_recorder"
    assert sanitized_rows(page)[0]["domain"] == "browser_recorder"
    assert "domain" not in broker.events(
        source="core_events", severity="", module="",
        since=None, until=None, limit=50, offset=0,
    )["events"][0]


@pytest.mark.parametrize("alias", [
    "Réconciliation", "Reconciliation", "Réconcilitation",
])
def test_reconciliation_aliases_are_exact_and_accent_independent(alias):
    broker = Broker([_row(1, domain="reconciliation", module="LIBRARY")])
    page = fetch_explorer_page(broker, domain=alias, period="all")
    assert page["total"] == 1
    assert broker.calls[0]["domain"] == "reconciliation"
    assert public_domain_id(alias) == "reconciliation"


@pytest.mark.parametrize("invalid", [
    None, True, "/home/PRIVATE", "Library", "PLUGIN", "my browser",
    "http://example.test/?token=PRIVATE", "browser" * 10,
])
def test_invalid_domain_does_not_read_broker(invalid):
    broker = Broker([])
    with pytest.raises(ValueError, match="unknown diagnostic domain"):
        fetch_explorer_page(broker, domain=invalid)
    assert broker.calls == []


def test_bulk_selection_keeps_exact_domain_and_checks_broker_response():
    broker = Broker([
        _row(1, domain="browser_recorder"),
        _row(2, domain="reconciliation", module="LIBRARY"),
    ])
    selected = collect_filtered_selection(
        broker, period="all", source="core_events",
        domain="Browser Recorder", severity="error", mode="all",
    )
    assert selected.count == 1
    packed = json.loads(selected.export_json)
    assert len(packed["events"]) == 1
    assert packed["events"][0]["event_id"] == "domain-1"
    assert [c["domain"] for c in broker.calls] == ["browser_recorder"] * 2

    with pytest.raises(ValueError, match="out-of-scope"):
        collect_filtered_selection(
            Broker([_row(9, domain="reconciliation", module="LIBRARY")],
                   ignore_filter=True),
            period="all", domain="browser", severity="error", mode="all",
        )


def test_malformed_core_domain_and_malicious_message_do_not_publish_label():
    fake = _row(1, domain="browser_recorder", module="LIBRARY")
    fake["message"] = "http://sample.test/?token=PRIVATE"
    rows = sanitized_rows({"events": [fake]}, include_detail=True)
    assert "domain" not in rows[0]
    assert "PRIVATE" not in json.dumps(rows)


def test_gtk_scope_is_explicit_and_ai_never_widens_filtered_request():
    root = Path(__file__).resolve().parents[1]
    ui = (root / "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    selection = (root / "diagnostics_selection.py").read_text(encoding="utf-8")
    ast.parse(ui)
    ast.parse(selection)
    assert 'domain_filter.connect("activate", reset_events)' in ui
    assert ui.count('"domain": domain_filter.get_text().strip()') == 2
    assert 'domain=domain_id' in selection
    assert 'if domain_filter.get_text().strip():' in ui
    assert "Aperçu IA indisponible avec filtre Domaine actif" in ui
    assert "Domaine métier" in ui


def test_legacy_core_unfiltered_works_but_domain_is_not_silently_ignored():
    class OldCore:
        def events(self, *, source, module, severity, since, until, limit, offset):
            return {"events": [], "offset": offset, "total": 0}
    assert fetch_explorer_page(OldCore(), period="all")["events"] == []
    with pytest.raises(TypeError):
        fetch_explorer_page(OldCore(), domain="Browser")
