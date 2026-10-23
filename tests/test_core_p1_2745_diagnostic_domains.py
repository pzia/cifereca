"""#2745: generic, Core-attested business domains are not log-text search."""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_domains import (
    DIAGNOSTIC_DOMAINS, canonical_diagnostic_domain,
)
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics
from tests.test_core_p0_2730_reconciliation_diagnostics import _audit
from tests.test_core_p0_2380_numilog_startup_provenance import _fields
from tests.test_core_p1_2888_download_broker_projection import payload
from tests.test_adept_shared_diagnostics import detail


@pytest.mark.parametrize(("given", "code"), [
    ("Browser", "browser_recorder"), ("recorder", "browser_recorder"),
    ("BROWSER RECORDER", "browser_recorder"),
    ("Réconciliation", "reconciliation"), ("Reconciliation", "reconciliation"),
    ("Réconcilitation", "reconciliation"),
    ("Connexion fournisseur", "provider_connection"),
    ("synchronisations", "source_sync"),
    ("Téléchargement de prêt", "loan_download"),
    ("sauvegarde kobo", "kobo_backup"),
    ("NUMILOG", "numilog"), ("ADEPT", "adept"),
])
def test_closed_domain_aliases(given, code):
    assert canonical_diagnostic_domain(given) == code


@pytest.mark.parametrize("invalid", [
    "../PRIVATE", "not_a_domain", "sync_all", "arbitrary provider",
    "account:alice", "browser_recorder.zip", True, [], None, "x" * 65,
])
def test_unknown_domain_is_rejected_before_reading_evidence(tmp_path, invalid):
    with pytest.raises(ValueError, match="unknown diagnostic domain"):
        query_diagnostic_events(
            Diagnostics(), source="core_events",
            log_path=tmp_path / "unread", domain=invalid,
        )


def test_each_registered_family_has_one_real_core_producer(tmp_path):
    diagnostics = Diagnostics(level="trace")
    diagnostics.add_core_browser_recorder_result("chromium_probe", "playwright_missing")
    diagnostics.add_core_reconciliation_result(
        _audit("incomplete_scan", ["time_budget"])
    )
    diagnostics.add_core_provider_connect_failure("numilog")
    diagnostics.add_core_source_sync(
        "numilog", phase="completed", status="success",
        code="source_sync_complete",
    )
    diagnostics.add_core_loan_download(payload())
    diagnostics.add_core_kobo_backup_decision({
        "status": "partial", "persisted_enabled": True,
        "connected_kobo": 3, "eligible_kobo": 3, "interval_minutes": 60,
    })
    diagnostics.add_core_numilog_startup(_fields())
    diagnostics.add_core_kobo_adept(
        detail("adept_error"), "error",
    )
    all_rows = query_diagnostic_events(
        diagnostics, log_path=tmp_path / "missing", source="core_events",
        limit=100,
    )
    grouped = {row["domain"]: row for row in all_rows["events"] if "domain" in row}
    assert set(grouped) == set(DIAGNOSTIC_DOMAINS)
    assert all(row["source"] == "core_events" for row in grouped.values())
    for domain in DIAGNOSTIC_DOMAINS:
        page = query_diagnostic_events(
            diagnostics, log_path=tmp_path / "missing",
            source="core_events", domain=domain, limit=1,
        )
        assert page["total"] == 1
        assert page["events"][0]["domain"] == domain
        assert page["next_offset"] is None
    assert "PRIVATE" not in json.dumps(all_rows)


def test_unverified_plugin_categories_never_enter_attested_families(tmp_path):
    diagnostics = Diagnostics()
    # 130 malicious lookalikes normally overwhelm the first 50 results.
    # Their labels and keys are NOT a Core attestation.
    for i in range(130):
        diagnostics.add(
            "PLUGIN", "browser recorder chromium verification", "error",
            detail={"domain": "browser_recorder", "operation": "reconciliation",
                    "account_id": "PRIVATE_CUSTOMER"},
        )
    diagnostics.add_core_browser_recorder_result("chromium_probe", "playwright_missing")
    path = tmp_path / "missing.log"
    unfiltered = query_diagnostic_events(
        diagnostics, source="core_events", log_path=path, limit=50,
    )
    assert unfiltered["total"] == 131
    browser = query_diagnostic_events(
        diagnostics, source="core_events", log_path=path, domain="Browser",
        limit=50,
    )
    assert browser["total"] == 1
    assert browser["events"][0]["domain"] == "browser_recorder"
    assert "PRIVATE_CUSTOMER" not in json.dumps(browser)
    for domain in ("reconciliation", "provider_connection"):
        assert query_diagnostic_events(
            diagnostics, source="core_events", log_path=path, domain=domain,
        )["total"] == 0
    # Module remains a separate technical filter; it does not assert family.
    assert query_diagnostic_events(
        diagnostics, source="core_events", log_path=path, module="PLUGIN",
        domain="browser_recorder",
    )["total"] == 1


def test_log_parser_attests_exact_schemas_but_not_injected_trace(tmp_path):
    log = tmp_path / "cifereca.log"
    safe = "TRACE kobo backup decision status=partial enabled=True connected=3 eligible=3 interval=60"
    log.write_text(
        "2026-10-20 13:54:51,293 INFO cifereca [Worker] " + safe + "\n"
        "2026-10-20 13:54:52,293 INFO cifereca.plugin [Worker] " + safe + "\n"
        "2026-10-20 13:54:53,293 INFO cifereca [Worker] "
        "TRACE kobo backup decision status=partial account=PRIVATE\n",
        encoding="utf-8",
    )
    result = query_diagnostic_events(
        Diagnostics(), log_path=log, source="app_log",
        domain="kobo_backup",
    )
    assert result["total"] == 1
    assert result["events"][0]["domain"] == "kobo_backup"
    assert result["events"][0]["business_module"] == "KOBO"
    assert "PRIVATE" not in json.dumps(result)
