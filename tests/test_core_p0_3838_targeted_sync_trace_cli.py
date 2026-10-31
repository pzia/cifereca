"""#3838: opt-in raw Trace of one exact CLI sync, never during global connect."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import cifereca.cli as cli
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level
import cifereca.http_trace as trace


def setup_cli(monkeypatch, *, status="partial"):
    activity = []
    account = SimpleNamespace(
        provider_id="numilog", identity_id="profile-test",
        credential_id="credential-test", unique_id="numilog:owned",
    )

    class Library:
        accounts = [account]
        def connect_configured(self):
            activity.append(("connect", diagnostics_level()))
        def synchronize_account_source(self, selected):
            assert selected is account
            activity.append(("run", diagnostics_level()))
            return {"status": status, "downloaded": 0,
                    "secret": "signed-url-synthetic-secret"}

    class Core:
        library = Library()
        storage = SimpleNamespace(
            provider_connections=lambda: [{
                "identity_id": "profile-test", "provider_id": "numilog",
                "credential_id": "credential-test", "enabled": True,
            }],
            provider_accounts=lambda: [{
                "identity_id": "profile-test", "provider_id": "numilog",
                "credential_id": "credential-test", "account_id": "numilog:owned",
            }],
        )
        def close(self):
            activity.append(("close", diagnostics_level()))

    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **kwargs: Core())
    return activity


def command(path=None, *, confirmation=True):
    args = ["sync", "run", "--provider", "numilog",
            "--identity", "profile-test", "--credential", "credential-test",
            "--account-id", "numilog:owned", "--json", "--confirm"]
    if path is not None:
        args.extend(["--trace-export", str(path)])
        if confirmation:
            args.append("--confirm-trace-export")
    return args


def test_raw_http_trace_is_exclusively_active_during_selected_run(monkeypatch, tmp_path, capsys):
    previous = diagnostics_level()
    set_diagnostics_level("normal")
    activity = setup_cli(monkeypatch)
    export = tmp_path / "private.zip"

    def write(path):
        activity.append(("export", diagnostics_level()))
        assert path == export
        assert diagnostics_level() == "trace"
        export.write_bytes(b"synthetic")
        return export

    monkeypatch.setattr(trace.HTTP_TRACE, "export", write)
    try:
        assert cli.main(command(export)) == 2  # partial stays non-success
        result = json.loads(capsys.readouterr().out)
        assert result == {"provider": "numilog", "identity": "profile-test",
                          "status": "partial", "downloaded": 0, "http_trace": "created"}
        assert activity == [
            ("connect", "normal"), ("run", "trace"),
            ("export", "trace"), ("close", "normal"),
        ]
        assert export.is_file()
    finally:
        set_diagnostics_level(previous)


def test_trace_requires_separate_confirmation_before_any_provider_activity(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kwargs: pytest.fail("preflight must reject before opening Core"),
    )
    assert cli.main(command(tmp_path / "private.zip", confirmation=False)) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "trace_export_confirmation_required"


def test_trace_export_error_does_not_claim_success_and_keeps_provider_result(monkeypatch, tmp_path, capsys):
    activity = setup_cli(monkeypatch, status="success")
    previous = diagnostics_level()
    set_diagnostics_level("normal")

    def refused(_destination):
        raise FileExistsError("synthetic permission or existing destination")

    monkeypatch.setattr(trace.HTTP_TRACE, "export", refused)
    try:
        assert cli.main(command(tmp_path / "existing.zip")) == 2
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "success"
        assert result["http_trace"] == "export_failed"
        assert "signed-url-synthetic-secret" not in json.dumps(result)
        assert activity[-1] == ("close", "normal")
    finally:
        set_diagnostics_level(previous)


def test_without_trace_flag_never_changes_observability_level(monkeypatch, capsys):
    activity = setup_cli(monkeypatch, status="success")
    previous = diagnostics_level()
    set_diagnostics_level("normal")
    try:
        assert cli.main(command()) == 0
        assert "http_trace" not in json.loads(capsys.readouterr().out)
        assert activity == [
            ("connect", "normal"), ("run", "normal"), ("close", "normal"),
        ]
    finally:
        set_diagnostics_level(previous)


def test_preexisting_trace_path_refused_before_provider_connection(monkeypatch, tmp_path, capsys):
    destination = tmp_path / "existing.zip"
    destination.write_bytes(b"untouched")
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kwargs: pytest.fail("preflight must reject before opening Core"),
    )
    assert cli.main(command(destination)) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "trace_export_destination_exists"
    assert destination.read_bytes() == b"untouched"
