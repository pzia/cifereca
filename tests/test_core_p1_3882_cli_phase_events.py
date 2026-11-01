"""#3882: CLI-observed milestones are bounded, closed and run-scoped."""
from __future__ import annotations

import json
from types import SimpleNamespace
import zipfile

from cifereca import cli
from cifereca.core.sync_report import write_sync_report
from tests.test_core_p0_3719_sync_cli_auto_select import make_cli_core


_ACCOUNT = SimpleNamespace(
    identity_id="PRIVATE_PROFILE", provider_id="numilog",
    credential_id="PRIVATE_CREDENTIAL", unique_id="PRIVATE_ACCOUNT",
)


def _archive_events(path):
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        events = [json.loads(line) for line in archive.read("events.jsonl").splitlines()]
        summary = archive.read("summary.txt").decode("ascii")
        all_bytes = b"".join(archive.read(name) for name in archive.namelist())
    return manifest, events, summary, all_bytes


def test_writer_rejects_untrusted_cli_event_fields_and_cross_run_refs(tmp_path):
    target = tmp_path / "safe.zip"
    write_sync_report(
        target, account=_ACCOUNT, run_ref="a" * 32,
        before={}, after={}, result={"status": "partial"},
        started_at="2026-11-01T12:00:00+00:00", exit_code=2,
        cli_events=[
            {"phase": "selected", "status": "success", "run_ref": "PRIVATE_OTHER_RUN",
             "task_ref": "PRIVATE_OTHER_TASK", "cookie": "PRIVATE_SESSION"},
            {"phase": "connect", "status": "failed", "code": "sync_unavailable",
             "exception": "PRIVATE_EXCEPTION", "url": "PRIVATE_SIGNED_URL"},
            {"phase": "PRIVATE_PHASE", "status": "success", "secret": "PRIVATE_TOKEN"},
            {"phase": "sync", "status": "PRIVATE_STATE", "path": "/PRIVATE_PATH"},
        ],
    )
    manifest, events, summary, whole = _archive_events(target)
    assert events == [
        {"run_ref": "a" * 32, "phase": "selected", "status": "success"},
        {"run_ref": "a" * 32, "phase": "connect", "status": "failed",
         "code": "sync_unavailable"},
        {"run_ref": "a" * 32, "phase": "run_finished", "status": "partial"},
    ]
    assert manifest["event_coverage"] == "cli_milestones_only"
    assert manifest["cli_events_truncated"] is True
    assert manifest["cli_last_phase"] == "connect"
    assert manifest["cli_issue_phase"] == "connect"
    assert "last_cli_phase: connect" in summary
    assert "issue_cli_phase: connect" in summary
    assert b"PRIVATE_" not in whole


def test_writer_bounds_event_count_without_reporting_complete_coverage(tmp_path):
    target = tmp_path / "bounded.zip"
    write_sync_report(
        target, account=_ACCOUNT, run_ref="b" * 32,
        before={}, after={}, result={"status": "failed", "code": "sync_unavailable"},
        started_at="start", exit_code=2,
        cli_events=[{"phase": "sync", "status": "started"}] * 50,
    )
    manifest, events, _, _ = _archive_events(target)
    assert len(events) == 33  # 32 observations plus terminal outcome
    assert manifest["cli_events_truncated"] is True
    assert all(item["run_ref"] == "b" * 32 for item in events)
    assert events[-1] == {"phase": "run_finished", "run_ref": "b" * 32,
                          "status": "failed", "code": "sync_unavailable"}


def test_real_cli_sync_milestones_are_scoped_and_do_not_claim_core_phases(
    monkeypatch, tmp_path, capsys,
):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "card-a", "account-a", True),
    ])
    target = tmp_path / "report.zip"
    assert cli.main([
        "sync", "run", "--provider", "numilog", "--identity", "profile-a",
        "--confirm", "--report", str(target),
    ]) == 0
    assert observed["connects"] == len(observed["runs"]) == 1
    assert json.loads(capsys.readouterr().out)["report"] == "created"
    manifest, events, summary, whole = _archive_events(target)
    assert [(e["phase"], e["status"]) for e in events] == [
        ("selected", "success"),
        ("audit_before", "unavailable"),
        ("connect", "started"), ("connect", "success"),
        ("sync", "started"), ("sync", "success"),
        ("audit_after", "unavailable"), ("run_finished", "success"),
    ]
    assert all(event["run_ref"] == manifest["run_ref"] for event in events)
    assert manifest["event_coverage"] == "cli_milestones_only"
    assert manifest["cli_last_phase"] == "audit_after"
    assert manifest["cli_issue_phase"] == "none"
    assert "last_cli_phase: audit_after" in summary
    assert b"card-a" not in whole
    assert b"account-a" not in whole
    assert b"profile-a" not in whole


def test_connection_exception_records_terminal_phase_without_fake_sync(
    monkeypatch, tmp_path, capsys,
):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "card-a", "account-a", True),
    ])
    core = cli.CiferecaCore.open()

    def fail_connect(**_kwargs):
        observed["connects"] += 1
        raise RuntimeError("PRIVATE_COOKIE_EXCEPTION")

    core.library.connect_selected_source = fail_connect
    target = tmp_path / "failed.zip"
    assert cli.main([
        "sync", "run", "--provider", "numilog", "--identity", "profile-a",
        "--confirm", "--report", str(target),
    ]) == 2
    assert observed["connects"] == 1 and observed["runs"] == []
    assert json.loads(capsys.readouterr().out)["report"] == "created"
    manifest, events, _, whole = _archive_events(target)
    assert [(e["phase"], e["status"]) for e in events] == [
        ("selected", "success"),
        ("audit_before", "unavailable"),
        ("connect", "started"), ("connect", "failed"),
        ("audit_after", "unavailable"), ("run_finished", "failed"),
    ]
    assert manifest["cli_last_phase"] == "audit_after"
    assert manifest["cli_issue_phase"] == "connect"
    assert events[3]["code"] == "sync_unavailable"
    assert b"PRIVATE_COOKIE_EXCEPTION" not in whole


def test_partial_business_result_records_closed_failure_phase(
    monkeypatch, tmp_path, capsys,
):
    make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "card-a", "account-a", True),
    ])
    core = cli.CiferecaCore.open()
    core.library.synchronize_account_source = lambda _account: {
        "status": "partial", "reason": "source_sync_partial",
        "errors": 1, "exception": "PRIVATE_PROVIDER_EXCEPTION",
    }
    target = tmp_path / "partial.zip"
    assert cli.main([
        "sync", "run", "--provider", "numilog", "--identity", "profile-a",
        "--confirm", "--report", str(target),
    ]) == 2
    assert json.loads(capsys.readouterr().out)["report"] == "created"
    manifest, events, summary, whole = _archive_events(target)
    assert manifest["cli_issue_phase"] == "sync"
    assert {"phase": "sync", "status": "partial",
            "code": "source_sync_partial", "run_ref": manifest["run_ref"]} in events
    assert events[-1]["code"] == "source_sync_partial"
    assert "issue_cli_phase: sync" in summary
    assert b"PRIVATE_PROVIDER_EXCEPTION" not in whole
