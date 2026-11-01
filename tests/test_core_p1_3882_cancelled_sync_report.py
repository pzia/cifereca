"""#3882: an interrupted admitted Sync run must export a closed cancelled ZIP."""
from __future__ import annotations

import json
from types import SimpleNamespace
import zipfile

import pytest

from cifereca import cli
from cifereca.core import sync_report
from tests.test_core_p0_3719_sync_cli_auto_select import make_cli_core


_ARGS = ("sync", "run", "--provider", "numilog", "--identity", "profile-a", "--confirm")


def _core(monkeypatch):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "card-a", "account-a", True),
    ])
    return cli.CiferecaCore.open(), observed


def _contents(path):
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        outcome = json.loads(archive.read("sync-result.json"))
        events = [json.loads(line) for line in archive.read("events.jsonl").splitlines()]
        before = json.loads(archive.read("acquisition-audit-before.json"))
        after = json.loads(archive.read("acquisition-audit-after.json"))
        summary = archive.read("summary.txt").decode("ascii")
        payload = b"".join(archive.read(name) for name in archive.namelist())
    return manifest, outcome, events, before, after, summary, payload


@pytest.mark.parametrize("phase", ["connect", "sync", "audit_before"])
def test_keyboard_interrupt_after_admission_creates_cancelled_zip(
    monkeypatch, tmp_path, capsys, phase,
):
    core, observed = _core(monkeypatch)

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt("PRIVATE_SESSION_COOKIE")

    if phase == "connect":
        core.library.connect_selected_source = interrupted
    elif phase == "sync":
        core.library.synchronize_account_source = interrupted
    else:
        monkeypatch.setattr(sync_report, "audit_source_counts", interrupted)

    path = tmp_path / "cancelled.zip"
    assert cli.main([*_ARGS, "--report", str(path)]) == 130
    assert path.is_file()
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "cancelled", "code": "cancelled", "report": "created"}
    manifest, result, events, before, after, summary, payload = _contents(path)
    assert manifest["exit_code"] == 130
    assert manifest["status"] == result["status"] == "cancelled"
    assert result["code"] == "cancelled"
    assert manifest["event_coverage"] == "cli_milestones_only"
    assert manifest["cli_issue_phase"] == phase
    assert {"run_ref": manifest["run_ref"], "phase": phase,
            "status": "cancelled", "code": "cancelled"} in events
    assert events[-1] == {
        "run_ref": manifest["run_ref"], "phase": "run_finished",
        "status": "cancelled", "code": "cancelled",
    }
    assert before["status"] == after["status"] == "unavailable"
    assert "issue_cli_phase: " + phase in summary
    assert b"PRIVATE_SESSION_COOKIE" not in payload
    for private in (b"profile-a", b"card-a", b"account-a"):
        assert private not in payload
    assert len(observed["runs"]) == (1 if phase == "sync" else 0)


def test_keyboard_interrupt_during_final_audit_keeps_prior_sync_proof(
    monkeypatch, tmp_path, capsys,
):
    core, observed = _core(monkeypatch)
    calls = 0

    def audit(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("PRIVATE_FINAL_AUDIT_EXCEPTION")
        return {"rights_by_kind": {"purchase": 3}, "origin_link_count": 1}

    monkeypatch.setattr(sync_report, "audit_source_counts", audit)
    target = tmp_path / "interrupted-after-sync.zip"
    assert cli.main([*_ARGS, "--report", str(target)]) == 130
    assert json.loads(capsys.readouterr().out)["report"] == "created"
    assert len(observed["runs"]) == calls - 1 == 1
    manifest, outcome, events, before, after, _, whole = _contents(target)
    assert outcome["status"] == "cancelled"
    assert manifest["cli_issue_phase"] == "audit_after"
    assert before["rights_by_kind"] == {"purchase": 3}
    assert after["status"] == "unavailable"
    assert {"run_ref": manifest["run_ref"], "phase": "sync",
            "status": "success"} in events
    assert {"run_ref": manifest["run_ref"], "phase": "audit_after",
            "status": "cancelled", "code": "cancelled"} in events
    assert b"PRIVATE_FINAL_AUDIT_EXCEPTION" not in whole


def test_keyboard_interrupt_during_optional_raw_trace_export_does_not_lose_report(
    monkeypatch, tmp_path, capsys,
):
    from cifereca.http_trace import HTTP_TRACE

    _, observed = _core(monkeypatch)

    def interrupted(_destination):
        raise KeyboardInterrupt("PRIVATE_RAW_HTTP_SESSION")

    monkeypatch.setattr(HTTP_TRACE, "export", interrupted)
    target = tmp_path / "closed.zip"
    raw = tmp_path / "raw.zip"
    assert cli.main([
        *_ARGS, "--report", str(target),
        "--trace-export", str(raw), "--confirm-trace-export",
    ]) == 130
    assert target.exists() and not raw.exists()
    output = json.loads(capsys.readouterr().out)
    assert output["report"] == "created"
    assert output["http_trace"] == "cancelled"
    assert output["status"] == "cancelled"
    manifest, outcome, events, _, _, _, whole = _contents(target)
    assert manifest["exit_code"] == 130
    assert manifest["cli_issue_phase"] == "trace_export"
    assert {"run_ref": manifest["run_ref"], "phase": "trace_export",
            "status": "cancelled"} in events
    assert outcome["status"] == "cancelled"
    assert b"PRIVATE_RAW_HTTP_SESSION" not in whole
    assert len(observed["runs"]) == 1


def test_interrupt_before_exact_scope_admission_never_publishes_report(
    monkeypatch, tmp_path, capsys,
):
    core, observed = _core(monkeypatch)

    def interrupted():
        raise KeyboardInterrupt("PRIVATE_EARLY")

    core.storage.provider_accounts = interrupted
    target = tmp_path / "non-admitted.zip"
    assert cli.main([*_ARGS, "--report", str(target)]) == 130
    assert not target.exists()
    assert observed["connects"] == 0 and not observed["runs"]
    assert b"PRIVATE_EARLY" not in capsys.readouterr().out.encode()


def test_cancelled_report_exit_is_explicitly_allowed_but_other_values_rejected(tmp_path):
    account = SimpleNamespace(identity_id="i", provider_id="p",
                              credential_id="c", unique_id="a")
    base = dict(
        account=account, run_ref="c" * 32,
        before={}, after={}, result={"status": "cancelled", "code": "cancelled"},
        started_at="start",
    )
    with pytest.raises(ValueError, match="sync_report_invalid_exit"):
        sync_report.write_sync_report(tmp_path / "never.zip", exit_code=131, **base)
    assert not (tmp_path / "never.zip").exists()
    path = tmp_path / "cancelled.zip"
    sync_report.write_sync_report(path, exit_code=130, **base)
    with zipfile.ZipFile(path) as z:
        assert json.loads(z.read("manifest.json"))["exit_code"] == 130
