"""#2745: only attested Core domains qualify for global diagnostics filtering."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.browser_recorder_diagnostics import OPERATIONS, public_fields
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics
from cifereca.gui.browser_recorder_diagnostics import emit_browser_recorder_diagnostic
from tests.test_core_p0_2730_reconciliation_diagnostics import _audit


@pytest.mark.parametrize("alias", (
    "Browser", "recorder", "Browser Recorder", "browser_recorder",
))
def test_browser_alias_filters_before_paging_and_cannot_be_spoofed(tmp_path, alias):
    diag = Diagnostics(max_entries=400)
    # 140 unrelated PLUGIN records push genuine Browser evidence well past
    # a default 50-row page. Identical message and detail are not attested.
    operation = OPERATIONS["chromium_install"]
    for i in range(140):
        diag.add("PLUGIN", operation, "error",
                 detail=public_fields("chromium_install", "download_failed"))
    emit_browser_recorder_diagnostic(
        SimpleNamespace(diagnostics=diag),
        "chromium_install", "download_failed",
    )
    diag.add("PLUGIN", "provider connect", "error",
             detail={"source": "PRIVATE_ACCOUNT_VALUE"})
    filtered = query_diagnostic_events(
        diag, log_path=tmp_path / "unused.log",
        source="all", domain=alias, limit=1, offset=0,
    )
    assert filtered["total"] == 1
    assert filtered["next_offset"] is None
    assert filtered["events"][0]["domain"] == "browser_recorder"
    assert filtered["events"][0]["severity"] == "error"
    assert filtered["events"][0]["detail"]["code"] == "download_failed"
    assert "PRIVATE_ACCOUNT_VALUE" not in json.dumps(filtered)


@pytest.mark.parametrize("alias", (
    "Réconciliation", "Reconciliation", "Réconcilitation", "reconciliation",
))
def test_reconciliation_alias_requires_attested_status_and_is_global(tmp_path, alias):
    diag = Diagnostics(max_entries=400)
    for i in range(121):
        diag.add("LIBRARY", "library_reconciliation_audit", "partial",
                 detail={"status": "partial", "operation": "library_reconciliation_audit"})
    diag.add_core_reconciliation_result(_audit("incomplete_scan", ["time_budget"]))
    diag.add_core_reconciliation_result(_audit("incomplete_scan", ["hash_byte_budget"]))
    filtered = query_diagnostic_events(
        diag, log_path=tmp_path / "missing.log",
        domain=alias, source="core_events", severity="warning", limit=1,
    )
    assert filtered["total"] == 2
    assert filtered["next_offset"] == 1
    second = query_diagnostic_events(
        diag, log_path=tmp_path / "missing.log",
        domain=alias, source="core_events", severity="warning",
        limit=1, offset=1, reason_code="hash_byte_budget",
    )
    assert second["total"] == 1
    assert second["events"][0]["domain"] == "reconciliation"
    assert second["events"][0]["detail"]["reasons"] == ["hash_byte_budget"]


def test_broker_domain_filter_returns_closed_typed_projection(tmp_path, monkeypatch):
    from cifereca import paths
    diag = Diagnostics()
    diag.add_core_reconciliation_result(_audit())
    emit_browser_recorder_diagnostic(
        SimpleNamespace(diagnostics=diag), "chromium_install", "ready",
    )
    monkeypatch.setattr(paths, "log_file", lambda: tmp_path / "no.log")
    service = SimpleNamespace(diagnostics=diag)
    for label, expected in (("recorder", "browser_recorder"),
                            ("Réconciliation", "reconciliation")):
        page = DiagnosticsBrokerImpl(service).events(
            domain=label, source="all",
        )
        assert page["total"] == 1
        assert page["events"][0]["domain"] == expected
        assert page["read_only"] is True


@pytest.mark.parametrize("bad", (
    True, None, [], {}, "my.private.account", "Browser Recorder PRIVATE",
    "BROWSER/../../SECRET", "plugin", "library", "browser" * 20,
))
def test_invalid_domain_fails_before_any_collector_or_log_io(tmp_path, bad):
    class NoIO:
        def snapshot(self):
            raise AssertionError("unexpected collector access")

    with pytest.raises(ValueError, match="unknown diagnostic domain"):
        query_diagnostic_events(
            NoIO(), log_path=tmp_path / "none.log", domain=bad,
        )


def test_empty_domain_keeps_old_public_broker_contract(tmp_path):
    diag = Diagnostics()
    diag.add("PLUGIN", "fixture_noise", "ok")
    implicit = query_diagnostic_events(diag, log_path=tmp_path / "absent.log",
                                       source="core_events")
    explicit = query_diagnostic_events(diag, log_path=tmp_path / "absent.log",
                                       source="core_events", domain="")
    assert implicit == explicit
