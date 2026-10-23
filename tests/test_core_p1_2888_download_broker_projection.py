"""#2888: a confirmed loan-download attempt appears in public Diagnostics.

Synthetic terminal facts only: no provider connection, HTTP request, ADEPT,
raw loan identifier, signed URL, account or private library access.
"""
from __future__ import annotations

import json
import logging

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_loan_download import log_message
from cifereca.diagnostics import Diagnostics


def payload(provider="paris", status="attention_required", code="acquisition_busy"):
    return {
        "schema_version": 1,
        "provider_id": provider,
        "operation_kind": "download_loan",
        "phase": "terminal",
        "status": status,
        "code": code,
        "duration_ms": 57,
    }


@pytest.fixture
def application_log(tmp_path):
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    handler = logging.FileHandler(path)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield path
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()


def test_provider_filter_across_both_channels_with_different_business_domains(application_log):
    diag = Diagnostics(level="trace")
    diag.add_core_loan_download(payload())
    diag.add_core_loan_download(payload("kobo", "success", "completed"))
    diag.add_core_loan_download(payload("paris", "failed", "provider_failure"))
    # A plugin may emit these same keys but cannot claim Core attestation.
    diag.add("PNB", "download_loan", "failed", detail=payload())
    all_rows = query_diagnostic_events(diag, log_path=application_log, limit=100)
    assert all_rows["total"] == 7  # 3 Core + 3 app_log + 1 generic
    paris = query_diagnostic_events(
        diag, log_path=application_log, provider_id="paris", limit=100,
    )
    assert paris["total"] == 4
    assert {r["source"] for r in paris["events"]} == {"app_log", "core_events"}
    assert {r["module"] for r in paris["events"]} == {"PNB", "cifereca"}
    assert all(r["provider_id"] == "paris" for r in paris["events"])
    assert all(r["detail"]["operation_kind"] == "download_loan" for r in paris["events"])
    assert {r["status"] for r in paris["events"]} == {"attention_required", "failed"}
    assert all(r["business_module"] == "PNB" for r in paris["events"]
               if r["source"] == "app_log")
    assert query_diagnostic_events(diag, log_path=application_log,
                                   provider_id="paris", module="PNB")["total"] == 2
    assert query_diagnostic_events(diag, log_path=application_log,
                                   provider_id="kobo")["total"] == 2
    assert "PRIVATE" not in json.dumps(paris, ensure_ascii=False)


def test_real_terminal_event_remains_visible_in_errors_only_and_without_collector(application_log):
    diag = Diagnostics(enabled=False)
    diag.add_core_loan_download(payload())
    events = query_diagnostic_events(
        diag, log_path=application_log, provider_id="paris"
    )
    assert events["status"] == "partial"
    assert events["total"] == 1
    assert events["events"][0]["source"] == "app_log"

    enabled = Diagnostics(level="errors")
    enabled.add_core_loan_download(payload("paris", "failed", "adept_error"))
    only = query_diagnostic_events(
        enabled, log_path=application_log, provider_id="paris",
        source="core_events",
    )
    assert only["total"] == 1
    assert only["events"][0]["detail"]["code"] == "adept_error"


def test_fake_plugin_log_and_generic_diagnostic_cannot_attest_provider(application_log):
    diag = Diagnostics()
    diag.add("PNB", "download_loan", "failed", detail=payload())
    line = log_message(payload())
    with application_log.open("a", encoding="utf-8") as handle:
        handle.write(f"2026-10-23 10:00:00,000 WARNING cifereca.plugin {line}\n")
        handle.write(f"2026-10-23 10:00:01,000 WARNING cifereca {line} PRIVATE_ACCOUNT\n")
        handle.write("2026-10-23 10:00:02,000 WARNING cifereca TRACE download loan provider_id=paris\n")
    events = query_diagnostic_events(diag, log_path=application_log, limit=100)
    assert events["total"] == 4
    assert not any("provider_id" in e for e in events["events"])
    assert query_diagnostic_events(
        diag, log_path=application_log, provider_id="paris"
    )["total"] == 0
    assert "PRIVATE_ACCOUNT" not in json.dumps(events)


def test_invalid_collector_attestation_cannot_escalate_to_core_event(tmp_path):
    diag = Diagnostics()
    diag.add_core_loan_download(payload())
    # Simulate poisoned legacy cache/metadata while keeping a real Diagnostics
    # instance; schema, business domain and status must still match.
    from dataclasses import replace
    with diag._lock:
        original = diag._items.pop()
        diag._items.append(replace(original, detail={
            **payload(), "private_url": "https://private.invalid/secret"
        }))
    rows = query_diagnostic_events(
        diag, log_path=tmp_path / "absent", source="core_events"
    )["events"]
    assert len(rows) == 1
    assert rows[0].get("provider_id") is None
    assert rows[0].get("detail") is None
    assert "https://" not in json.dumps(rows)
    # Another business category cannot be used to claim a Paris loan.
    with diag._lock:
        diag._items.clear()
        diag._items.append(replace(original, category="LOAN"))
    rows = query_diagnostic_events(
        diag, log_path=tmp_path / "absent", source="core_events"
    )["events"]
    assert "provider_id" not in rows[0]
