"""#3882: real CLI admission/report lifecycle, with no provider-specific behavior."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from cifereca import cli
from cifereca.core import acquisition_audit as audit_core
from cifereca.core import acquisition_audit_cli as offline_cli


def _empty_audit():
    return {
        "schema_version": 1, "scope_ref": "a" * 24,
        "snapshot_availability": "available",
        "source_count": 0, "receipt_count": 0,
        "counts": {}, "sources": [], "receipts": [],
        "coverage": {"staging": "configured_roots_only", "filesystem": "sha_checked"},
    }


def _fake_core(monkeypatch, tmp_path, *, sync_failure=False, before_failure=False):
    observed = {"connect": 0, "runs": 0, "audits": 0}
    storage = SimpleNamespace(
        path=tmp_path / "library.sqlite3",
        provider_connections=lambda: [{
            "identity_id": "alice", "provider_id": "numilog",
            "credential_id": "credential-secret", "enabled": True,
        }],
        provider_accounts=lambda: [{
            "identity_id": "alice", "provider_id": "numilog",
            "credential_id": "credential-secret", "account_id": "account-private",
        }],
    )
    account = SimpleNamespace(identity_id="alice", provider_id="numilog",
                              credential_id="credential-secret",
                              unique_id="account-private")
    book_dir = tmp_path / "EBooks"
    book_dir.mkdir()
    def connect(**kwargs):
        observed["connect"] += 1
    def sync(_account):
        observed["runs"] += 1
        if sync_failure:
            raise RuntimeError("Authorization: Bearer SECRET-TOKEN")
        return {"status": "partial", "scanned": 6, "downloaded": 4,
                "reason": "source_sync_partial", "private": "SECRET-TOKEN"}
    service = SimpleNamespace(
        accounts=[account], connect_selected_source=connect,
        synchronize_account_source=sync, library_dir=lambda: book_dir,
    )
    core = SimpleNamespace(storage=storage, library=service, close=lambda: None)
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **kw: core)

    def run_audit(*args, **kwargs):
        observed["audits"] += 1
        if before_failure and observed["audits"] >= 2:
            raise OSError("missing-mount")
        return _empty_audit()
    monkeypatch.setattr(audit_core, "audit_account", run_audit)
    return observed


def test_sync_report_partial_produces_full_bundle(tmp_path, monkeypatch, capsys):
    observed = _fake_core(monkeypatch, tmp_path)
    target = tmp_path / "sync.zip"
    rc = cli.main(["sync", "run", "--provider", "numilog", "--identity",
                   "alice", "--confirm", "--report", str(target)])
    assert rc == 2
    assert observed == {"connect": 1, "runs": 1, "audits": 2}
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "partial" and response["report"] == "created"
    with ZipFile(target) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["status"] == "partial"
        assert manifest["source_scope_ref"] == "a" * 24
        assert manifest["http_trace_included"] is False
        assert manifest["run_ref"]
        assert json.loads(z.read("acquisition-audit-before.json"))["source_count"] == 0
        assert json.loads(z.read("acquisition-audit-after.json"))["source_count"] == 0
        events = [json.loads(line) for line in z.read("events.jsonl").splitlines()]
        assert len(events) == 2 and len({e["run_ref"] for e in events}) == 1
        text = b"".join(z.read(name) for name in z.namelist())
        for private in (b"credential-secret", b"account-private", b"SECRET-TOKEN"):
            assert private not in text


def test_sync_failure_after_admission_still_reports(tmp_path, monkeypatch, capsys):
    observed = _fake_core(monkeypatch, tmp_path, sync_failure=True)
    target = tmp_path / "failed.zip"
    assert cli.main(["sync", "run", "--provider", "numilog", "--identity",
                     "alice", "--confirm", "--report", str(target)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and result["report"] == "created"
    assert observed["audits"] == 2
    with ZipFile(target) as z:
        assert json.loads(z.read("manifest.json"))["status"] == "failed"
        assert b"SECRET-TOKEN" not in b"".join(z.read(n) for n in z.namelist())


def test_failed_after_audit_does_not_invent_empty_observation(tmp_path, monkeypatch):
    _fake_core(monkeypatch, tmp_path, before_failure=True)
    target = tmp_path / "unknown-after.zip"
    cli.main(["sync", "run", "--provider", "numilog", "--identity",
              "alice", "--confirm", "--report", str(target)])
    with ZipFile(target) as z:
        after = json.loads(z.read("acquisition-audit-after.json"))
        assert after["snapshot_availability"] == "unavailable"
        assert after["coverage"]["source_references"] == "unavailable"


def test_preflight_report_exists_or_missing_confirm_refuses_before_network(
    tmp_path, monkeypatch, capsys,
):
    observed = _fake_core(monkeypatch, tmp_path)
    target = tmp_path / "exists.zip"
    target.write_bytes(b"KEEP")
    for options, code in (
        (["--confirm", "--report", str(target)], "report_destination_exists"),
        (["--report", str(tmp_path / "new.zip")],
         "explicit_run_confirmation_required"),
    ):
        assert cli.main(["sync", "run", "--provider", "numilog",
                         "--identity", "alice", *options]) == 2
        assert json.loads(capsys.readouterr().out)["code"] == code
    assert observed["connect"] == observed["runs"] == 0
    assert target.read_bytes() == b"KEEP"


def test_preflight_report_and_http_trace_same_path_refused(tmp_path, monkeypatch, capsys):
    observed = _fake_core(monkeypatch, tmp_path)
    target = tmp_path / "combined.zip"
    assert cli.main([
        "sync", "run", "--provider", "numilog", "--identity", "alice", "--confirm",
        "--report", str(target), "--trace-export", str(target),
        "--confirm-trace-export",
    ]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "report_trace_destination_conflict"
    assert observed["runs"] == 0 and not target.exists()


def test_offline_command_uses_mode_ro_without_core_open(tmp_path, monkeypatch, capsys):
    import sqlite3

    db_file = tmp_path / "library.sqlite3"
    with sqlite3.connect(db_file) as db:
        db.execute("CREATE TABLE provider_connections(connection_id TEXT,identity_id TEXT,provider_id TEXT,credential_id TEXT,enabled INTEGER)")
        db.execute("CREATE TABLE provider_accounts(connection_id TEXT,identity_id TEXT,provider_id TEXT,credential_id TEXT,account_id TEXT)")
        db.execute("INSERT INTO provider_connections VALUES('c','alice','numilog','credential-secret',1)")
        db.execute("INSERT INTO provider_accounts VALUES('c','alice','numilog','credential-secret','account-private')")
    monkeypatch.setattr(offline_cli, "database_file", lambda **kw: db_file)
    monkeypatch.setattr(offline_cli, "_library_root", lambda: tmp_path / "EBooks")
    monkeypatch.setattr(cli.CiferecaCore, "open",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("Core.open forbidden")))

    def audit_mock(storage, **scope):
        with storage._connect() as readonly:
            with pytest.raises(sqlite3.OperationalError):
                readonly.execute("CREATE TABLE impossible(x)")
        return _empty_audit()
    monkeypatch.setattr(offline_cli, "audit_account", audit_mock)
    target = tmp_path / "audit.json"
    assert cli.main(["acquisition-audit", "--provider", "numilog",
                     "--identity", "alice", "--json", "--output", str(target)]) == 0
    assert target.exists()
    payload = json.loads(target.read_text())
    assert payload["source_count"] == 0
    assert "credential-secret" not in target.read_text()
    assert json.loads(capsys.readouterr().out)["source_count"] == 0
