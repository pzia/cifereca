"""Synthetic, offline Numilog journal diagnosis contract (#2797/#3838)."""
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


MODULE = Path(__file__).resolve().parents[1] / "numilog_delivery_diagnose.py"
spec = importlib.util.spec_from_file_location("numilog_delivery_diagnose", MODULE)
diagnose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnose)


def event(action, phase, code, **extra):
    return {"timestamp": "2026-10-31T08:00:00+00:00", "module": "NUMILOG",
            "operation": "acquisition_delivery", "status": "ok",
            "task_ref": "synthetic-task", "detail": {
                "action": action, "source": "owned_card", "phase": phase,
                "code": code, **extra}}


def journal(events):
    return {"format": "cifereca-raw-journal-events", "schema_version": 1, "events": events}


def test_six_acquisitions_two_success_four_http_refusals_not_placement():
    events = []
    for i in range(6):
        action = f"{i + 1:012x}"
        events.extend([
            event(action, "start", "started"),
            event(action, "delivery_http", "http_ok" if i < 2 else "http_error",
                  origin_kind="other", path_kind="other", hops=2,
                  http_status=200 if i < 2 else 502,
                  mime="acsm_xml", cookie_header_present=False),
            event(action, "completed", "acsm_complete" if i < 2 else "http_error", hops=2),
        ])
    events.append({"timestamp": "2026-10-31T08:00:01+00:00",
                   "message": "synthetic secret token=HIDDEN_123"})
    report = diagnose.analyze(journal(events))
    assert report["coverage"]["delivery_attempts"] == 6
    assert report["counts"] == {
        "delivery_complete_unverified_placement": 2, "http_refusal": 4,
    }
    failed = report["attempts"][2]
    assert failed["http_hops"][0]["http_status"] == 502
    assert failed["http_hops"][0]["origin_kind"] == "other"
    assert not failed["http_hops"][0]["cookie_header_present"]
    assert "HIDDEN_123" not in json.dumps(report)
    assert "placement" in report["coverage"]["note"].lower()


def test_no_terminal_and_acsm_candidate_never_claim_physical_epub():
    report = diagnose.analyze(journal([
        event("000000000001", "start", "started"),
        event("000000000002", "completed", "acsm_candidate"),
        event("000000000003", "completed", "origin_rejected"),
    ]))
    assert report["counts"] == {
        "acsm_candidate_requires_core_adept": 1,
        "incomplete_trace": 1,
        "redirect_or_origin_guard": 1,
    }


def test_untrusted_fields_cannot_enter_diagnostic_report():
    report = diagnose.analyze(journal([
        event("000000000001", "delivery_http", "http_unknown-secret",
              origin_kind="private.example/?token=HIDDEN_123",
              mime="HIDDEN_123", http_status="403",
              headers={"Authorization": "Bearer HIDDEN_123"}),
    ]))
    assert "HIDDEN_123" not in json.dumps(report)
    assert report["attempts"][0]["http_hops"][0]["code"] == "unclassified_error"
    assert report["attempts"][0]["http_hops"][0]["origin_kind"] == "unknown"


def test_exact_task_filter_and_legacy_diag_json():
    raw = {"timestamp": "2026-10-31T08:00:02+00:00", "message": "DIAG_JSON " + json.dumps({
        "category": "NUMILOG", "operation": "acquisition_delivery",
        "detail": {"action": "000000000002", "phase": "completed", "code": "epub_complete"}})}
    report = diagnose.analyze(journal([
        event("000000000001", "completed", "http_forbidden", hops=1), raw,
    ]), task_ref="synthetic-task")
    assert report["coverage"]["delivery_attempts"] == 1
    assert report["attempts"][0]["terminal_code"] == "http_forbidden"


def test_valid_and_invalid_raw_zip(tmp_path, capsys):
    zip_path = tmp_path / "raw.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("journal.json", json.dumps(journal([
            event("000000000001", "completed", "http_error")])) )
    assert diagnose.main([str(zip_path), "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["counts"] == {"http_refusal_without_hop": 1}
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("a.json", "{}")
        archive.writestr("b.json", "{}")
    assert diagnose.main([str(bad)]) == 2
    assert "unexpected_journal_archive" in capsys.readouterr().err


def test_old_journal_without_delivery_never_claims_success():
    report = diagnose.analyze(journal([
        {"timestamp": "2026-10-29T06:00:00+00:00",
         "module": "NUMILOG", "operation": "HTTP", "detail": {"status": 200}}
    ]))
    assert report["coverage"]["delivery_attempts"] == 0
    assert not report["counts"]
