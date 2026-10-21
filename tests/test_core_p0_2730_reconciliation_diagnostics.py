"""#2730: one attested reconciliation terminal event, no private book evidence."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca.core import library_audit_broker as broker
from cifereca.core.diagnostic_reconciliation import (
    OPERATION, from_audit_result, public_fields,
)
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics


SECRET = "PRIVATE_BOOK_2730 ISBN 9780306406157 /home/private/library"


def _audit(status="complete", reasons=None):
    return {
        "status": status, "reasons": [] if reasons is None else reasons,
        "inventory": {
            "files_started": 321, "files": 320, "hashed_files": 320,
            "entries": 322,
            "phase_ms": {
                "sha256": 8100, "epub_opf": 1100,
                "structure": 3000, "pdf_signature": 0,
            },
            "phase_calls": {
                "sha256": 321, "epub_opf": 320,
                "structure": 320, "pdf_signature": 0,
            },
            "title": SECRET,
        },
        "metadata_candidates": {
            "catalog_rows_examined": 522, "references_examined": 730,
            "files_examined": 320, "metadata_candidates": 0,
            "index_ms": 750, "match_ms": 900, "private_isbn": SECRET,
        },
        "reconciliation": {"counts": {"rights_without_verified_file": 408}},
        "private_file_path": SECRET,
    }


def _events(diag, tmp_path):
    return query_diagnostic_events(
        diag, source="core_events", log_path=tmp_path / "unused.log",
        module="LIBRARY",
    )["events"]


def test_complete_bounded_aggregate_projection_without_private_fields():
    fields = from_audit_result(_audit(), 134325)
    assert public_fields(fields) == fields
    assert fields["operation"] == OPERATION
    assert fields["status"] == "complete"
    assert fields["code"] == "none"
    assert fields["duration_ms"] == 134325
    assert fields["counts"]["files_started"] == 321
    assert fields["counts"]["files_observed"] == 320
    assert fields["counts"]["files_hashed"] == 320
    assert fields["counts"]["catalog_rows_examined"] == 522
    assert fields["counts"]["references_examined"] == 730
    assert fields["phase_ms"]["sha256"] == 8100
    assert fields["phase_ms"]["candidate_match"] == 900
    assert fields["phase_calls"]["epub_opf"] == 320
    assert SECRET not in json.dumps(fields)


@pytest.mark.parametrize(("reasons", "expected"), [
    (["time_budget", "epub_parser_limit_or_invalid"], "partial"),
    (["structure_limit_or_invalid"], "partial"),
    (["metadata_candidate_time_budget"], "partial"),
    (["snapshot_time_budget"], "partial"),
    (["audit_cancelled"], "cancelled"),
    (["reconciliation_scope_changed"], "failed"),
    (["reconciliation_unavailable"], "failed"),
    ([SECRET], "partial"),
])
def test_incomplete_never_means_zero_candidates_proves_absence(reasons, expected):
    fields = from_audit_result(_audit("incomplete_scan", reasons))
    assert fields["status"] == expected
    assert public_fields(fields) == fields
    assert fields["counts"]["metadata_candidates"] == 0
    assert SECRET not in json.dumps(fields)
    if SECRET in reasons:
        assert fields["code"] == "unclassified_limit"


def test_invalid_numbers_and_hostile_report_cannot_leak():
    report = _audit("incomplete_scan", ["time_budget"])
    report["inventory"]["files_started"] = True
    report["inventory"]["hashed_files"] = SECRET
    report["inventory"]["phase_ms"]["sha256"] = -5
    report["metadata_candidates"]["catalog_rows_examined"] = float("inf")
    fields = from_audit_result(report, SECRET)
    assert "duration_ms" not in fields
    assert "files_started" not in fields["counts"]
    assert "files_hashed" not in fields["counts"]
    assert "catalog_rows_examined" not in fields["counts"]
    assert "sha256" not in fields["phase_ms"]
    assert SECRET not in json.dumps(fields)
    forged = dict(fields, private_path=SECRET, counts={"private_path": SECRET})
    assert "private_path" not in json.dumps(public_fields(forged))


def test_broker_publishes_one_terminal_diagnostic_and_keeps_audit_result(monkeypatch, tmp_path):
    result = _audit("incomplete_scan", [
        "time_budget", "epub_parser_limit_or_invalid",
        "structure_limit_or_invalid", "metadata_candidate_time_budget",
    ])
    diag = Diagnostics()
    monkeypatch.setattr(broker, "_execute_scoped_audit", lambda *args: result)
    assert broker._run_scoped_audit(SimpleNamespace(diagnostics=diag), "synthetic", None, None) is result
    assert len(diag.snapshot()) == 1
    row, = _events(diag, tmp_path)
    assert row["source"] == "core_events"
    assert row["module"] == "LIBRARY"
    assert row["severity"] == "warning"
    assert row["status"] == "partial"
    assert row["detail"]["counts"]["files_started"] == 321
    assert row["detail"]["phase_ms"]["epub_opf"] == 1100
    assert "metadata_candidate_time_budget" in row["detail"]["reasons"]
    assert SECRET not in diag.text("synthetic")
    assert SECRET not in json.dumps(row)


def test_private_inspection_and_generic_spoof_are_not_attested(monkeypatch, tmp_path):
    diag = Diagnostics()
    monkeypatch.setattr(broker, "_execute_scoped_audit", lambda *args: object())
    broker._run_scoped_audit(SimpleNamespace(diagnostics=diag), "synthetic", None, None)
    assert diag.snapshot() == []
    forged = from_audit_result(_audit("incomplete_scan", ["time_budget"]))
    diag.add("LIBRARY", OPERATION, "partial", detail=forged)
    row, = _events(diag, tmp_path)
    assert "detail" not in row
    assert "files_started" not in json.dumps(row)
    assert diag.snapshot()[0].get("verified_reconciliation") is None


def test_error_only_records_partial_cancelled_and_failed_but_not_complete(tmp_path):
    diag = Diagnostics(level="errors")
    for status, reasons in (
        ("complete", []),
        ("incomplete_scan", ["time_budget"]),
        ("incomplete_scan", ["audit_cancelled"]),
        ("incomplete_scan", ["reconciliation_unavailable"]),
    ):
        diag.add_core_reconciliation_result(_audit(status, reasons), duration_ms=120000)
    rows = _events(diag, tmp_path)
    assert len(rows) == 3
    assert {row["status"] for row in rows} == {"partial", "cancelled", "failed"}
    assert {row["severity"] for row in rows} == {"warning", "error"}


def test_optional_diagnostics_never_perturbs_broker_result(monkeypatch):
    result = _audit()
    diag = Diagnostics()
    monkeypatch.setattr(broker, "_execute_scoped_audit", lambda *args: result)

    def failed_sink(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(diag, "add_core_reconciliation_result", failed_sink)
    assert broker._run_scoped_audit(SimpleNamespace(diagnostics=diag), "synthetic", None, None) is result
    assert diag.snapshot() == []


def test_complete_emits_info_and_keeps_phase_count_scope(tmp_path):
    diag = Diagnostics()
    diag.add_core_reconciliation_result(_audit(), duration_ms=300)
    row, = _events(diag, tmp_path)
    assert row["severity"] == "info"
    assert row["status"] == "complete"
    assert row["detail"]["phase"] == "finished"
    assert row["detail"]["duration_ms"] == 300
    assert row["detail"]["counts"]["files_started"] == 321
    assert "rights_without_verified_file" not in row["detail"]["counts"]
