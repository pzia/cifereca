"""#2888: retain Core-attested Numilog preflight in provider-scoped Diagnostics.

A blocked materialization with attempted=0 is not a failed download/GET.
All inputs are synthetic; the real Core producer is exercised without a
provider session, SQLite mutation, acquisition or network access.
"""
from __future__ import annotations

import copy
import json
import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.diagnostic_journal import query_journal, journal_text_export
from cifereca.core.numilog_startup_diagnostics import StartupTrace
from cifereca.diagnostics import Diagnostics
from tests.test_diagnostics_acquisition_fidelity import journal


def producer(journal, *, phase="completed", code="preflight_failed", status="blocked"):
    storage = NS(metadata_get=lambda key: None)
    collector = Diagnostics()
    service = NS(storage=storage, diagnostics=collector)
    trace = StartupTrace(service, "sync:" + "a" * 24)
    original = trace.emit(phase, code, status)
    return collector, original


@pytest.mark.parametrize("phase,code,status,expected_status,expected_severity", [
    ("completed", "preflight_failed", "blocked", "warning", "warning"),
    ("completed", "acquisition_failed", "failed", "error", "error"),
    ("completed", "manifest_failed", "partial", "error", "warning"),
    ("completed", "complete", "complete", "ok", "info"),
    ("preflight", "started", "running", "ok", "info"),
])
def test_real_core_startup_producer_preserves_provider_without_download_claim(
    journal, phase, code, status, expected_status, expected_severity,
):
    collector, detail = producer(journal, phase=phase, code=code, status=status)
    original = copy.deepcopy(detail)
    selected = query_journal(journal.path, provider_id="numilog", limit=100)
    assert selected["total"] == 1
    (event,) = selected["events"]
    assert event["provider_id"] == "numilog"
    assert event["provider_id_origin"] == "core_numilog_startup_observation"
    assert event["module"] == "NUMILOG"
    assert event["operation"] == "startup_materialization"
    assert event["status"] == expected_status
    assert event["severity"] == expected_severity
    assert event["detail"] == original
    assert event["detail"]["attempted"] == 0
    assert event["detail"]["downloaded"] == 0
    assert event.get("task_ref") == detail.get("task_ref")
    assert query_journal(journal.path, provider_id="paris")["total"] == 0
    assert detail == original

    broker = DiagnosticsBrokerImpl(NS(diagnostics=collector))
    assert broker.events(provider_id="numilog")["events"] == [event]
    assert event in json.loads(broker.export_raw_journal())["events"]
    ndjson = [json.loads(line) for line in journal_text_export(
        journal.path, version="test",
    ).splitlines() if line.startswith("{")]
    assert event in ndjson


@pytest.mark.parametrize("mutation", [
    {"phase": "strange_phase"},
    {"code": "strange_code"},
    {"status": "strange"},
    {"attempted": "zero"},
    {"downloaded": 99},
    {"failed": 99},
    {"task_kind": "other"},
    {"credentials": "synthetic-password"},
])
def test_unvalidated_generic_numilog_payload_never_gets_provider(journal, mutation):
    _collector, valid = producer(journal, phase="preflight", code="started", status="running")
    # Reuse the exact observed schema and falsify a single field; a later
    # plugin-supplied shape must not gain a provider through its module name.
    malformed = {**valid, **mutation}
    diag = Diagnostics()
    diag.add("NUMILOG", "startup_materialization", "ok", detail=malformed)
    events = query_journal(journal.path, limit=100)["events"]
    assert len(events) == 2
    assert events[0]["provider_id"] == "numilog"
    assert "provider_id" not in events[1]
    assert query_journal(journal.path, provider_id="numilog")["total"] == 1


def test_wrong_level_and_different_logger_cannot_claim_numilog(journal):
    _collector, fields = producer(journal, phase="completed", code="preflight_failed", status="blocked")
    # First record is the real producer and remains filterable.
    diag = Diagnostics()
    diag.add("NUMILOG", "startup_materialization", "error", detail=fields)
    plugin = logging.getLogger("cifereca.plugin.provider.numilog")
    plugin.warning("DIAG_JSON %s", json.dumps({
        "schema_version": 1, "category": "NUMILOG",
        "operation": "startup_materialization", "status": "warning",
        "message": "startup_materialization", "detail": fields,
    }))
    events = query_journal(journal.path, limit=100)["events"]
    assert len(events) == 3
    assert "provider_id" not in events[1]
    assert "provider_id" not in events[2]
    assert query_journal(journal.path, provider_id="numilog")["events"] == [events[0]]


def test_unrelated_numilog_message_does_not_claim_materialization_provider(journal):
    diag = Diagnostics()
    diag.add("NUMILOG", "session_state", "warning", detail={
        "status": "blocked", "code": "preflight_failed",
    })
    (event,) = query_journal(journal.path)["events"]
    assert "provider_id" not in event
