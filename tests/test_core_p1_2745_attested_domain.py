"""#2745: bounded, globally paginated business domains from Core provenance.

The dataset and log path are synthetic. No HTTP bodies, credentials or book
titles are ever eligible as domain evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from cifereca.core.diagnostic_event_feed import (
    canonical_diagnostic_domain, query_diagnostic_events,
)
from cifereca.diagnostics import Diagnostics
from tests.test_core_p0_2730_reconciliation_diagnostics import _audit


def _events(tmp_path, diagnostics, **filters):
    return query_diagnostic_events(
        diagnostics, log_path=tmp_path / "nonexistent.log",
        source="core_events", **filters,
    )


@pytest.mark.parametrize("alias,canonical", [
    ("browser", "browser_recorder"), ("Recorder", "browser_recorder"),
    ("Browser Recorder", "browser_recorder"),
    ("browser_recorder", "browser_recorder"),
    ("réconciliation", "reconciliation"),
    ("Reconciliation", "reconciliation"),
    ("Réconcilitation", "reconciliation"),
    ("", ""),
])
def test_fixed_business_domain_aliases(alias, canonical):
    assert canonical_diagnostic_domain(alias) == canonical


@pytest.mark.parametrize("invalid", [
    None, False, 12, "store", "PLUGIN", "Library", "Paris",
    "https://signed.example/file?token=PRIVATE",
    "browser" * 30, "recorder\x00",
])
def test_invalid_domains_rejected_before_any_source_read(tmp_path, invalid):
    class ExplodingDiagnostics:
        def snapshot(self):
            raise AssertionError("broker read despite invalid filter")

    with pytest.raises(ValueError, match="unknown diagnostic domain"):
        _events(tmp_path, ExplodingDiagnostics(), domain=invalid)


def test_attested_domain_filter_matches_globally_before_pagination(tmp_path):
    diagnostics = Diagnostics()
    for _ in range(130):
        diagnostics.add("PLUGIN", "unverified browser recorder", "error")
    diagnostics.add_core_browser_recorder_result("chromium_install", "ready")
    for _ in range(130):
        diagnostics.add("LIBRARY", "library_reconciliation_audit", "partial",
                        detail={"operation": "library_reconciliation_audit"})
    diagnostics.add_core_reconciliation_result(_audit("incomplete_scan", ["time_budget"]),
                                               duration_ms=100)
    for _ in range(110):
        diagnostics.add("PLUGIN", "browser recorder fake", "error")
    full = _events(tmp_path, diagnostics, limit=50)
    assert full["total"] >= 372

    browser = _events(tmp_path, diagnostics, domain="Recorder", limit=1)
    assert browser["total"] == 1
    assert browser["next_offset"] is None
    browser_row, = browser["events"]
    assert browser_row["module"] == "PLUGIN"
    assert browser_row["domain"] == "browser_recorder"

    recon = _events(tmp_path, diagnostics, domain="Réconcilitation", limit=1)
    assert recon["total"] == 1
    row, = recon["events"]
    assert row["module"] == "LIBRARY"
    assert row["domain"] == "reconciliation"
    assert row["severity"] == "warning"
    assert row["status"] == "partial"

    assert _events(tmp_path, diagnostics, domain="browser", severity="warning")["total"] == 0
    since_future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    assert _events(tmp_path, diagnostics, domain="reconciliation",
                   since=since_future)["total"] == 0
    assert _events(tmp_path, diagnostics, domain="reconciliation",
                   module="PLUGIN")["total"] == 0
    published = json.dumps(browser) + json.dumps(recon)
    assert "PRIVATE" not in published


def test_generic_plugin_and_library_rows_cannot_spoof_attested_domain(tmp_path):
    diagnostics = Diagnostics()
    diagnostics.add("PLUGIN", "chromium install", "error", detail={
        "plugin_id": "browser-recorder", "code": "ready",
        "password": "PRIVATE_SECRET",
    })
    diagnostics.add("LIBRARY", "library_reconciliation_audit", "partial",
                    detail={"status": "partial", "title": "PRIVATE_TITLE"})
    assert _events(tmp_path, diagnostics, domain="browser")["total"] == 0
    assert _events(tmp_path, diagnostics, domain="reconciliation")["total"] == 0
    unfiltered = _events(tmp_path, diagnostics)["events"]
    assert all("domain" not in item for item in unfiltered)
    assert "PRIVATE" not in json.dumps(unfiltered)
