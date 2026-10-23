"""#2745: exact business domains are Core attestations, never text guesses."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca.core.browser_recorder_diagnostics import OPERATIONS, public_fields
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_reconciliation import OPERATION, from_audit_result
from cifereca.diagnostics import Diagnostics
from cifereca.gui.browser_recorder_diagnostics import emit_browser_recorder_diagnostic
from tests.test_core_p0_2730_reconciliation_diagnostics import _audit


def _feed(diagnostics, tmp_path, **filters):
    return query_diagnostic_events(
        diagnostics, log_path=tmp_path / "absent.log",
        source="core_events", **filters,
    )


def test_attested_domain_filters_before_pagination_not_by_plugin_category(tmp_path):
    diagnostics = Diagnostics()
    for index in range(120):
        diagnostics.add("PLUGIN", f"browser recorder fake operation {index}",
                        "error", detail={"private": "PRIVATE_BOOK_TOKEN"})
        diagnostics.add("LIBRARY", f"réconciliation fake operation {index}",
                        "warning", detail={"private": "PRIVATE_BOOK_TOKEN"})
    emit_browser_recorder_diagnostic(
        SimpleNamespace(diagnostics=diagnostics), "chromium_install", "ready"
    )
    emit_browser_recorder_diagnostic(
        SimpleNamespace(diagnostics=diagnostics), "chromium_install", "download_failed"
    )
    diagnostics.add_core_reconciliation_result(
        _audit("incomplete_scan", ["time_budget"]), duration_ms=300,
    )
    first = _feed(diagnostics, tmp_path, domain="browser_recorder", limit=1)
    second = _feed(diagnostics, tmp_path, domain="browser_recorder", limit=1, offset=1)
    assert first["total"] == second["total"] == 2
    assert first["next_offset"] == 1 and second["next_offset"] is None
    assert {first["events"][0]["severity"], second["events"][0]["severity"]} == {
        "info", "error",
    }
    assert {first["events"][0]["domain"], second["events"][0]["domain"]} == {
        "browser_recorder",
    }
    reconciliation = _feed(diagnostics, tmp_path, domain="reconciliation")
    assert reconciliation["total"] == 1
    assert reconciliation["events"][0]["domain"] == "reconciliation"
    assert reconciliation["events"][0]["severity"] == "warning"
    assert reconciliation["events"][0]["status"] == "partial"
    assert _feed(diagnostics, tmp_path, domain="browser_recorder", severity="error")["total"] == 1
    assert "PRIVATE_BOOK_TOKEN" not in json.dumps(first)
    assert "PRIVATE_BOOK_TOKEN" not in json.dumps(reconciliation)


def test_forged_category_operation_and_detail_cannot_set_domain(tmp_path):
    diagnostics = Diagnostics()
    operation = OPERATIONS["chromium_install"]
    diagnostics.add("PLUGIN", operation, "ok",
                    detail=public_fields("chromium_install", "ready"))
    fields = from_audit_result(_audit("incomplete_scan", ["time_budget"]))
    diagnostics.add("LIBRARY", OPERATION, "partial", detail=fields)
    unfiltered = _feed(diagnostics, tmp_path)
    assert unfiltered["total"] == 2
    assert all("domain" not in event for event in unfiltered["events"])
    assert _feed(diagnostics, tmp_path, domain="browser_recorder")["total"] == 0
    assert _feed(diagnostics, tmp_path, domain="reconciliation")["total"] == 0


@pytest.mark.parametrize("bad", [
    "browser", "recorder", "reconciliation", "Réconciliation", "ALL", "x" * 70,
    True, None, [], {}, "browser_recorder; private", "PRIVATE_KEY",
])
def test_core_refuses_untrusted_or_noncanonical_domain(tmp_path, bad):
    diagnostics = Diagnostics()
    with pytest.raises(ValueError, match="unknown diagnostic domain"):
        _feed(diagnostics, tmp_path, domain=bad)


def test_default_legacy_domain_keeps_unfiltered_categories(tmp_path):
    diagnostics = Diagnostics()
    diagnostics.add("CORE", "fixed test", "ok")
    assert _feed(diagnostics, tmp_path)["total"] == 1
    assert _feed(diagnostics, tmp_path, domain="")["total"] == 1
