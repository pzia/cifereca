"""#2745: domain aliases must filter attested Core events before pagination."""
from __future__ import annotations

import pytest

from cifereca.core.browser_recorder_diagnostics import OPERATIONS, public_fields
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics


@pytest.mark.parametrize("alias", ["Browser", "recorder", "browser recorder", "BROWSER"])
def test_browser_domain_is_global_and_rejects_plugin_impostors(tmp_path, alias):
    diagnostics = Diagnostics()
    for n in range(120):
        diagnostics.add("PLUGIN", "unrelated operation", "ok", detail={"n": n})
    # Same-looking operation and detail are not Core attestation.
    diagnostics.add(
        "PLUGIN", OPERATIONS["chromium_install"], "ok",
        detail=public_fields("chromium_install", "ready"),
    )
    diagnostics.add_core_browser_recorder_result("chromium_install", "ready")
    page = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "absent", module=alias,
        source="all", limit=1, offset=0,
    )
    assert page["total"] == 1
    assert len(page["events"]) == 1
    assert page["events"][0]["detail"]["operation"] == "chromium_install"
    assert page["events"][0]["source"] == "core_events"
    assert page["next_offset"] is None


def test_domain_alias_does_not_reinterpret_normal_module_prefix(tmp_path):
    diagnostics = Diagnostics()
    diagnostics.add("PLUGIN", "other", "ok")
    feed = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "absent", module="PLUGIN",
        source="core_events",
    )
    assert feed["total"] == 1


@pytest.mark.parametrize("alias", ["Réconciliation", "Reconciliation", "Réconcilitation"])
def test_reconciliation_alias_does_not_match_unverified_library_event(
        tmp_path, alias):
    diagnostics = Diagnostics()
    diagnostics.add(
        "LIBRARY", "library_reconciliation_audit", "partial",
        detail={"operation": "library_reconciliation_audit", "status": "partial"},
    )
    feed = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "absent", module=alias,
        source="core_events",
    )
    assert feed["total"] == 0
