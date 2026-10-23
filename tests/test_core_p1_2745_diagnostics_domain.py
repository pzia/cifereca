"""#2745: Core-attested business filters are global, not message searches.

Synthetic event producers only; no private browser capture, account or book.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca.core.browser_recorder_diagnostics import OPERATIONS, public_fields
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics
from tests.test_core_p0_2730_reconciliation_diagnostics import _audit


def _read(diagnostics, tmp_path, **filters):
    return query_diagnostic_events(
        diagnostics, log_path=tmp_path / "MISSING_SYNTHETIC_LOG",
        source="core_events", **filters,
    )


def test_domains_are_verified_before_global_pagination_and_source_coverage(tmp_path):
    diagnostics = Diagnostics()
    for _ in range(115):
        diagnostics.add("PLUGIN", "generic plugin task", "error")
    diagnostics.add("PLUGIN", OPERATIONS["chromium_install"], "error",
                    detail=public_fields("chromium_install", "download_failed"))
    diagnostics.add_core_browser_recorder_result("chromium_install", "download_failed")
    for _ in range(117):
        diagnostics.add("LIBRARY", "generic library task", "warning")
    diagnostics.add("LIBRARY", "library_reconciliation_audit", "partial",
                    detail={"status": "partial", "operation": "library_reconciliation_audit"})
    diagnostics.add_core_reconciliation_result(_audit("incomplete_scan", ["time_budget"]))
    diagnostics.add_core_reconciliation_result(_audit(), duration_ms=250)

    browser = _read(diagnostics, tmp_path, domain="browser_recorder")
    assert browser["total"] == 1 and len(browser["events"]) == 1
    assert browser["events"][0]["domain"] == "browser_recorder"
    assert browser["events"][0]["module"] == "PLUGIN"
    assert browser["events"][0]["severity"] == "error"
    assert any("domain" not in row for row in _read(diagnostics, tmp_path, limit=100)["events"])

    recon = _read(diagnostics, tmp_path, domain="reconciliation", limit=1)
    assert recon["total"] == 2 and recon["next_offset"] == 1
    more = _read(diagnostics, tmp_path, domain="reconciliation", limit=1, offset=1)
    assert more["total"] == 2 and more["next_offset"] is None
    assert {row["severity"] for row in recon["events"] + more["events"]} == {
        "warning", "info",
    }
    assert all(row["domain"] == "reconciliation"
               and row["module"] == "LIBRARY"
               for row in recon["events"] + more["events"])
    # Filtering provenance must never publish any private source detail.
    for page in (browser, recon, more):
        assert page["read_only"] is True
        assert "PRIVATE_BOOK" not in json.dumps(page)
        assert "MISSING_SYNTHETIC_LOG" not in json.dumps(page)


@pytest.mark.parametrize("value", ["Browser", "recorder", "LIBRARY", "anything",
                                     "réconciliation", True, None, 1, []])
def test_core_rejects_aliases_and_malformed_domain_inputs(tmp_path, value):
    diagnostics = Diagnostics()
    with pytest.raises(ValueError, match="domain"):
        _read(diagnostics, tmp_path, domain=value)


def test_other_collector_cannot_claim_core_attestation_even_with_flag(tmp_path):
    class ForgedCollector:
        enabled = True

        def snapshot(self):
            return [{
                "timestamp": "2026-10-23T12:00:00+00:00",
                "category": "PLUGIN",
                "operation": OPERATIONS["chromium_install"],
                "status": "error",
                "detail": public_fields("chromium_install", "download_failed"),
                "verified_browser_recorder": True,
            }]

    result = _read(ForgedCollector(), tmp_path, domain="browser_recorder")
    assert result["total"] == 0
    raw = _read(ForgedCollector(), tmp_path)
    assert raw["total"] == 1
    assert raw["events"][0]["module"] == "PLUGIN"
    assert "domain" not in raw["events"][0]


def test_domain_and_severity_and_period_compose_before_page(tmp_path):
    diagnostics = Diagnostics()
    for _ in range(75):
        diagnostics.add("PLUGIN", "generic", "error")
    diagnostics.add_core_browser_recorder_result("chromium_install", "ready")
    diagnostics.add_core_browser_recorder_result("chromium_install", "download_failed")
    all_domain = _read(diagnostics, tmp_path, domain="browser_recorder",
                       severity="error", limit=1)
    assert all_domain["total"] == 1
    assert all_domain["events"][0]["status"] in {"error", "failed"}
    future = _read(diagnostics, tmp_path, domain="browser_recorder",
                   since="2099-01-01T00:00:00+00:00")
    assert future["total"] == 0
