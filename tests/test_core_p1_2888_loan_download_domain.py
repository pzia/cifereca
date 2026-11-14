"""#2888: only Core-attested terminal loan downloads enter the domain filter.

Synthetic events only. Their provider, status and code are finite and never
recovered from a filename, private loan ID, logger prose or a plugin message.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics


def terminal(provider, *, status, code):
    return {
        "schema_version": 1, "operation_kind": "download_loan",
        "provider_id": provider, "phase": "terminal",
        "status": status, "code": code, "duration_ms": 10,
    }


def test_loan_domain_is_core_attested_global_and_provider_scoped(tmp_path):
    diagnostics = Diagnostics(enabled=True)
    for _ in range(125):
        # Even a forged exact operation/provider/status cannot confer
        # provenance or enter the pre-pagination domain filter.
        diagnostics.add("PNB", "download_loan", "failed", detail=terminal(
            "paris", status="failed", code="provider_failure",
        ))
    diagnostics.add_core_loan_download(
        terminal("paris", status="failed", code="provider_failure")
    )
    diagnostics.add_core_loan_download(
        terminal("numilog", status="success", code="completed")
    )
    log = tmp_path / "EMPTY_APP_LOG"
    log.write_text("")

    first = query_diagnostic_events(
        diagnostics, log_path=log, source="all",
        domain="loan_download", limit=1, offset=0,
    )
    assert first["total"] == 2
    assert len(first["events"]) == 1
    second = query_diagnostic_events(
        diagnostics, log_path=log, source="all",
        domain="loan_download", limit=1, offset=1,
    )
    rows = first["events"] + second["events"]
    assert {row["module"] for row in rows} == {"LOAN"}
    assert {row["domain"] for row in rows} == {"loan_download"}
    assert {row["provider_id"] for row in rows} == {"paris", "numilog"}
    assert all(row["source"] == "core_events" for row in rows)
    assert all("PRIVATE" not in json.dumps(row) for row in rows)
    assert all(row["detail"]["operation_kind"] == "download_loan"
               for row in rows)
    assert query_diagnostic_events(
        diagnostics, log_path=log, domain="loan_download",
        provider_id="paris",
    )["total"] == 1
    assert query_diagnostic_events(
        diagnostics, log_path=log, domain="loan_download", severity="error",
    )["total"] == 1
    assert query_diagnostic_events(
        diagnostics, log_path=log, domain="loan_download", severity="info",
    )["total"] == 1
    assert query_diagnostic_events(
        diagnostics, log_path=log, source="app_log", domain="loan_download",
    )["total"] == 0


def test_generic_attestation_in_detail_never_gets_loan_domain(tmp_path):
    diagnostics = Diagnostics(enabled=True)
    diagnostics.add(
        "PNB", "download_loan", "failed",
        detail={**terminal("paris", status="failed",
                           code="provider_failure"),
                "verified_loan_download": True},
    )
    result = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "missing.log", source="core_events",
        domain="loan_download",
    )
    assert result["total"] == 0
    all_events = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "missing.log", source="core_events",
    )["events"]
    assert len(all_events) == 1
    assert "domain" not in all_events[0]


@pytest.mark.parametrize("invalid", ["PRIVATE-ACCOUNT", "plugin", True, None])
def test_unknown_business_domain_fails_closed(tmp_path, invalid):
    with pytest.raises(ValueError, match="unknown diagnostic domain"):
        query_diagnostic_events(
            Diagnostics(), log_path=tmp_path / "missing.log", domain=invalid,
        )


def test_legacy_pnb_remains_readable_but_cannot_claim_loan_domain(tmp_path):
    """Retired provider category cannot imply typed Core download authority."""
    from dataclasses import replace

    diagnostics = Diagnostics(enabled=True)
    diagnostics.add_core_loan_download(
        terminal("paris", status="failed", code="provider_failure")
    )
    diagnostics.add_core_loan_download(
        terminal("numilog", status="success", code="completed")
    )
    with diagnostics._lock:
        paris, other = diagnostics._items
        assert paris.category == "LOAN" and other.category == "LOAN"
        diagnostics._items[0] = replace(paris, category="PNB")
        diagnostics._items[1] = replace(other, category="PNB")

    report = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "missing.log",
        domain="loan_download", source="core_events",
    )
    assert report["total"] == 0
    assert report["events"] == []
    # The former event is still visible in the general, private-safe feed;
    # it must not be promoted to the canonical domain or given provider proof.
    all_events = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "missing.log", source="core_events",
    )["events"]
    assert len(all_events) == 2
    assert all(row["module"] == "PNB" for row in all_events)
    assert all("domain" not in row for row in all_events)
    assert all("detail" not in row for row in all_events)
    assert all("provider_id" not in row for row in all_events)


def test_new_paris_download_is_in_neutral_loan_domain(tmp_path):
    diagnostics = Diagnostics(enabled=True)
    diagnostics.add_core_loan_download(
        terminal("paris", status="failed", code="provider_failure")
    )
    (row,) = diagnostics.snapshot()
    assert row["category"] == "LOAN"
    report = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "missing.log",
        domain="loan_download", source="core_events", provider_id="paris",
    )
    assert report["total"] == 1
    assert report["events"][0]["module"] == "LOAN"
