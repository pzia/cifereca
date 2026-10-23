"""#2745: Core-only verified domains, aliases, and global filtering."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from cifereca.core.browser_recorder_diagnostics import PLUGIN_ID, OPERATIONS, public_fields
from cifereca.core.diagnostic_event_feed import (
    canonical_diagnostic_domain, query_diagnostic_events,
)
from cifereca.core.diagnostic_reconciliation import OPERATION, from_audit_result
from cifereca.diagnostics import Diagnostics
from cifereca.plugins.manager import PluginManager
from tests.test_core_p0_2730_reconciliation_diagnostics import _audit
from tests.test_browser_recorder_private_zip_diagnostics import manager


def _page(collector, tmp_path, **filters):
    return query_diagnostic_events(
        collector, log_path=tmp_path / "PRIVATE_UNUSED.log",
        source="core_events", **filters,
    )


def _browser(collector, *, failed=False):
    code = "private_zip_export_failed" if failed else "private_zip_exported"
    PluginManager._plugin_diag(
        manager(collector), PLUGIN_ID, "PRIVATE ignored", "error",
        {"operation": "private_zip_export", "code": code, "output_bytes": 14},
        "normal",
    )


@pytest.mark.parametrize(("typed", "normalized"), [
    ("Browser", "browser_recorder"), ("recorder", "browser_recorder"),
    ("Browser Recorder", "browser_recorder"),
    ("BROWSER_RECORDER", "browser_recorder"),
    ("Réconciliation", "reconciliation"),
    ("Reconciliation", "reconciliation"),
    ("Réconcilitation", "reconciliation"),
    ("RECONCILITATION", "reconciliation"),
    ("", ""),
])
def test_only_known_aliases_are_accepted(typed, normalized):
    assert canonical_diagnostic_domain(typed) == normalized


@pytest.mark.parametrize("bad", [
    None, True, {}, ["recorder"], "PLUGIN", "LIBRARY", "browsers",
    "PRIVATE/recorder", "browser recorder secret", "a" * 41,
])
def test_invalid_domain_rejected_before_any_collector_access(tmp_path, bad):
    class NoSnapshot:
        def snapshot(self):
            raise AssertionError("collector accessed before validation")

    with pytest.raises(ValueError, match="unknown diagnostic domain"):
        _page(NoSnapshot(), tmp_path, domain=bad)


def test_browser_domain_is_attested_before_global_pagination(tmp_path):
    collector = Diagnostics()
    # Known category and same message are not verified Browser Recorder.
    for _ in range(110):
        collector.add("PLUGIN", OPERATIONS["private_zip_export"], "error",
                      detail=public_fields("private_zip_export",
                                           "private_zip_export_failed"))
    _browser(collector)
    _browser(collector, failed=True)
    all_rows = _page(collector, tmp_path, limit=50)
    assert all_rows["total"] == 112
    first = _page(collector, tmp_path, domain="Browser", limit=1)
    next_page = _page(collector, tmp_path, domain="recorder", limit=1, offset=1)
    assert first["total"] == next_page["total"] == 2
    assert first["next_offset"] == 1 and next_page["next_offset"] is None
    rows = first["events"] + next_page["events"]
    assert {row["domain"] for row in rows} == {"browser_recorder"}
    assert {row["severity"] for row in rows} == {"info", "error"}
    assert len({row["event_id"] for row in rows}) == 2
    assert not any(row.get("domain") for row in all_rows["events"][:50])
    assert "PRIVATE" not in json.dumps(first) + json.dumps(next_page)


def test_reconciliation_domain_requires_real_core_attestation(tmp_path):
    diag = Diagnostics()
    forged = from_audit_result(_audit("incomplete_scan", ["time_budget"]))
    for _ in range(110):
        diag.add("LIBRARY", OPERATION, "partial", detail=forged)
    diag.add_core_reconciliation_result(_audit("incomplete_scan", ["time_budget"]))
    diag.add_core_reconciliation_result(_audit(), duration_ms=17)
    result = _page(diag, tmp_path, domain="Réconcilitation", limit=10)
    assert result["total"] == 2
    assert {row["domain"] for row in result["events"]} == {"reconciliation"}
    assert {row["status"] for row in result["events"]} == {"partial", "complete"}
    assert {row["severity"] for row in result["events"]} == {"warning", "info"}
    warning = _page(diag, tmp_path, domain="Reconciliation", severity="warning")
    assert warning["total"] == 1
    assert "time_budget" in warning["events"][0]["detail"]["reasons"]
    assert _page(diag, tmp_path, domain="browser")["total"] == 0


def test_blank_domain_is_backwards_compatible_and_not_a_module_alias(tmp_path):
    collector = Diagnostics()
    collector.add("PLUGIN", "browser recorder", "error")
    _browser(collector)
    default = _page(collector, tmp_path)
    explicit = _page(collector, tmp_path, domain="")
    assert default == explicit
    assert _page(collector, tmp_path, module="PLUGIN", domain="recorder")["total"] == 1
    assert _page(collector, tmp_path, module="PLUGIN", domain="reconciliation")["total"] == 0


def test_real_broker_forwards_attested_domain_without_supplier_access(tmp_path):
    from unittest.mock import patch
    from cifereca.brokers import DiagnosticsBrokerImpl

    diag = Diagnostics()
    for _ in range(51):
        diag.add("PLUGIN", OPERATIONS["private_zip_export"], "error")
    _browser(diag)
    service = NS(diagnostics=diag)
    with patch("cifereca.paths.log_file", return_value=tmp_path / "absent.log"):
        result = DiagnosticsBrokerImpl(service).events(
            source="all", domain="recorder", limit=1, offset=0,
        )
    assert result["total"] == 1
    assert result["events"][0]["domain"] == "browser_recorder"
    assert result["events"][0]["source"] == "core_events"
    assert result["events"][0]["module"] == "PLUGIN"
    assert "PRIVATE" not in json.dumps(result)
