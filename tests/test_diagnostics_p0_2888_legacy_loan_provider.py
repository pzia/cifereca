"""#2888: preserve the *observed* provider on Core opening-loan warnings.

The production producer in sync_coordination.py emits `issue["provider"]`
rather than `provider_id`. These are synthetic replicas of its exact
envelopes; no supplier GET, permission change, or rights inference occurs.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.diagnostic_journal import query_journal, journal_text_export
from cifereca.diagnostics import Diagnostics
from tests.test_diagnostics_acquisition_fidelity import journal


def opening_issue(provider="paris", status="attention_required", code="adept_error"):
    return {
        "provider": provider, "loan_id": "synthetic-loan-1",
        "title": "Synthetic Book", "status": status, "code": code,
        "error": None, "process": {},
    }


@pytest.mark.parametrize("provider,status,code,level", [
    ("paris", "attention_required", "adept_loan_identity_conflict", "warning"),
    ("paris", "attention_required", "adept_error", "warning"),
    ("bnf-pret", "attention_required", "adept_error", "warning"),
    ("paris", "retryable_error", "provider_failure", "error"),
])
def test_core_opening_loan_issue_is_filterable_by_exact_observed_provider(
    journal, provider, status, code, level,
):
    diag = Diagnostics(level="errors")
    original = opening_issue(provider, status, code)
    diag.add("ACQUISITION", "loan sync", level, detail=original, level="errors")
    found = query_journal(journal.path, provider_id=provider)
    assert found["total"] == 1
    event = found["events"][0]
    assert event["provider_id"] == provider
    assert event["provider_id_origin"] == "legacy_opening_loan_sync"
    assert event["detail"] == original
    assert event["status"] == level
    assert event["module"] == "ACQUISITION"
    assert not event.get("task_ref")  # Generic opening sync is not a worker GET.
    assert query_journal(journal.path, provider_id="numilog")["total"] == 0

    broker = DiagnosticsBrokerImpl(NS(diagnostics=diag))
    assert broker.events(provider_id=provider)["events"] == [event]
    assert event in json.loads(broker.export_raw_journal())["events"]
    ndjson = [json.loads(line) for line in journal_text_export(
        journal.path, version="test",
    ).splitlines() if line.startswith("{")]
    assert event in ndjson
    assert original == opening_issue(provider, status, code)


@pytest.mark.parametrize("mutation", [
    {"category": "PNB"},
    {"operation": "loan_auto"},
    {"status": "success"},
    {"provider": "numilog"},
    {"provider": "Paris"},
    {"provider": "primary"},
    {"provider": "credential_123"},
    {"provider": None},
    {"issue_status": "retryable_error"},
    {"code": ""},
    {"loan_id": None},
])
def test_unscoped_or_contradictory_legacy_detail_never_becomes_provider(
    journal, mutation,
):
    issue = opening_issue()
    issue.update({
        key: value for key, value in mutation.items()
        if key not in {"category", "operation", "status", "issue_status"}
    })
    if "issue_status" in mutation:
        issue["status"] = mutation["issue_status"]
    diag = Diagnostics()
    diag.add(
        mutation.get("category", "ACQUISITION"),
        mutation.get("operation", "loan sync"),
        mutation.get("status", "warning"),
        detail=issue,
    )
    (event,) = query_journal(journal.path)["events"]
    assert "provider_id" not in event
    assert "provider_id_origin" not in event
    assert query_journal(journal.path, provider_id="paris")["total"] == 0


def test_identical_plugin_logger_line_cannot_promote_legacy_provider(journal):
    payload = {
        "schema_version": 1, "category": "ACQUISITION",
        "operation": "loan sync", "status": "warning",
        "detail": opening_issue(), "message": "loan sync",
    }
    plugin = logging.getLogger("cifereca.plugin.provider.paris")
    plugin.warning("DIAG_JSON %s", json.dumps(payload))
    (event,) = query_journal(journal.path)["events"]
    assert event["logger"] == "cifereca.plugin.provider.paris"
    assert "provider_id" not in event
    assert "provider_id_origin" not in event


def test_automatic_loan_admission_cause_does_not_gain_provider_from_context(journal):
    diag = Diagnostics()
    diag.add("ACQUISITION", "loan_download_cause", "warning", detail={
        "provider": "paris", "loan_ref": "synthetic-loan-1",
        "code": "cross_identity_loan", "phase": "admission",
    })
    (event,) = query_journal(journal.path)["events"]
    assert "provider_id" not in event
    assert query_journal(journal.path, provider_id="paris")["total"] == 0


def test_explicit_canonical_provider_id_keeps_original_authority(journal):
    diag = Diagnostics()
    diag.add("SYNC", "source sync", "failed", detail={
        "provider_id": "numilog", "status": "failed", "code": "operation_busy",
    })
    (event,) = query_journal(journal.path, provider_id="numilog")["events"]
    assert event["provider_id"] == "numilog"
    assert "provider_id_origin" not in event
