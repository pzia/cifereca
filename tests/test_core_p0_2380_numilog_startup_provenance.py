"""P0 #2380: typed Numilog startup is Core-attested, not plugin-supplied.

All observations and refs are synthetic. No network, account or receipt.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.numilog_startup_diagnostics import StartupTrace
from cifereca.diagnostics import Diagnostics


def _fields():
    return {
        "phase": "completed", "cause_phase": "acquisition",
        "code": "acquisition_failed", "status": "failed",
        "task_kind": "source-materialize",
        "attempted": 1, "downloaded": 0, "skipped": 0, "failed": 1,
        "task_ref": "123456789abc",
    }


def test_generic_numilog_entry_cannot_publish_unattested_startup_fact(tmp_path):
    diag = Diagnostics()
    diag.add("NUMILOG", "startup_materialization", "error", detail=_fields())
    row, = query_diagnostic_events(
        diag, source="core_events", log_path=tmp_path / "unread",
    )["events"]
    assert row["module"] == "NUMILOG"
    assert row["message"] == "[opération masquée]"
    assert "detail" not in row
    assert "task_ref" not in row
    assert "acquisition_failed" not in json.dumps(row)


def test_real_startup_trace_preserves_checked_facts_and_attestation(tmp_path):
    diag = Diagnostics()
    service = SimpleNamespace(
        diagnostics=diag, storage=SimpleNamespace(metadata_get=lambda _: None),
    )
    trace = StartupTrace(service, "invalid-synthetic-source")
    trace.phase = "acquisition"
    trace.fail("acquisition_failed")
    trace.counts.update(attempted=1, failed=1)
    published = trace.finish("failed")
    assert published["code"] == "acquisition_failed"
    stored, = diag.snapshot()
    assert stored["verified_numilog_startup"] is True
    row, = query_diagnostic_events(
        diag, source="core_events", log_path=tmp_path / "unread",
    )["events"]
    assert row["detail"] == published
    assert row["status"] == "failed"
    assert row["message"].startswith("startup_materialization")
    assert "task_ref" not in published


def test_malformed_attested_fields_are_not_persisted_or_displayed(tmp_path):
    diag = Diagnostics()
    bad = {**_fields(), "token": "https://private.invalid/?secret=NEVER"}
    diag.add_core_numilog_startup(bad)
    assert diag.snapshot() == []
    diag.add("NUMILOG", "startup_materialization", "error", detail=bad)
    output = query_diagnostic_events(
        diag, source="core_events", log_path=tmp_path / "unread",
    )
    assert output["total"] == 1
    assert "detail" not in output["events"][0]
    assert "NEVER" not in json.dumps(output)


def test_attested_producer_respects_disabled_collector():
    diag = Diagnostics(enabled=False)
    diag.add_core_numilog_startup(_fields())
    assert diag.snapshot() == []
