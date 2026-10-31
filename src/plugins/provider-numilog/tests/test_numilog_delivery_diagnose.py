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


def startup(run_ref, phase, code, *, positions=(), task="synthetic-task",
            attempted=0, downloaded=0, skipped=0, failed=0, status="running"):
    return {
        "timestamp": "2026-10-29T06:01:36+00:00",
        "module": "NUMILOG", "operation": "startup_materialization",
        "task_ref": task,
        "detail": {
            "run_ref": run_ref, "task_ref": task, "task_kind": "source-materialize",
            "phase": phase, "cause_phase": "preflight", "code": code,
            "status": status, "attempted": attempted, "downloaded": downloaded,
            "skipped": skipped, "failed": failed,
            "item_decisions": [
                {"position": i, "decision": "already_local_verified" if i <= 3
                 else "needs_reconciliation"} for i in positions
            ],
        },
    }


def test_historical_six_purchases_exposes_preflight_block_before_any_http():
    rows = [
        startup("010203040506", "preflight", "started", positions=(1, 2, 3),
                skipped=3),
        startup("010203040506", "completed", "preflight_failed",
                positions=(1, 2, 3, 4, 5, 6), status="blocked", skipped=3),
    ]
    report = diagnose.analyze(journal(rows))
    assert report["coverage"]["delivery_attempts"] == 0
    assert report["coverage"]["startup_runs"] == 1
    run = report["startup_runs"][0]
    assert run["terminal_code"] == "preflight_failed"
    assert run["status"] == "blocked"
    assert run["attempted"] == run["downloaded"] == 0
    assert run["skipped"] == 3
    assert [item["decision"] for item in run["item_decisions"]] == (
        ["already_local_verified"] * 3 + ["needs_reconciliation"] * 3
    )


def test_preflight_fields_are_closed_and_task_scoped():
    malicious = startup("010203040506", "completed", "preflight_failed",
                        positions=(1,), status="blocked")
    malicious["detail"]["item_decisions"][0]["decision"] = (
        "url=https://private.example/?token=SYNTHETIC_SECRET"
    )
    other = startup("abcdefabcdef", "completed", "complete",
                    task="another-task", status="complete")
    report = diagnose.analyze(journal([malicious, other]),
                              task_ref="synthetic-task")
    assert report["coverage"]["startup_runs"] == 1
    assert report["startup_runs"][0]["item_decisions"] == []
    assert "SYNTHETIC_SECRET" not in json.dumps(report)
