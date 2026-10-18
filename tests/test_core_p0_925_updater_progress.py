"""P0 #925: actual updater stages, redaction and single-document JSON stdout.

No GitHub, venv, pip, plugin installation or release is executed here.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace as NS

import pytest

from cifereca import dev_updater, updater
from cifereca.update_progress import update_operation, update_phase, step


class FlushingStream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushes = 0

    def flush(self):
        self.flushes += 1
        super().flush()


def test_scoped_progress_emits_start_stage_and_terminal_done_with_flush():
    output, events = FlushingStream(), []
    with update_operation("archive", stream=output, callback=events.append):
        assert step("metadata", lambda x: x + 1, 2) == 3
        with update_phase("source_copy"):
            pass
    assert [e["status"] for e in events] == [
        "START", "RUNNING", "OK", "RUNNING", "OK", "DONE",
    ]
    assert [e["stage"] for e in events] == [
        "operation", "metadata", "metadata", "source_copy",
        "source_copy", "operation",
    ]
    assert events[-1]["outcome"] == "success"
    assert len({e["transaction_ref"] for e in events}) == 1
    assert events[0]["schema"] == "cifereca.update.progress/1"
    assert all(e["elapsed_ms"] >= 0 and e["duration_ms"] >= 0 for e in events)
    assert output.flushes == len(events)
    assert "archive/source_copy" in output.getvalue()


def test_failure_has_stage_error_and_failed_terminal_without_raw_exception():
    secret = "https://private.invalid/install?token=SECRET_VALUE"
    events, output = [], FlushingStream()
    with pytest.raises(RuntimeError):
        with update_operation("dev_plugins_only", stream=output, callback=events.append):
            step("plugin_install", lambda: (_ for _ in ()).throw(RuntimeError(secret)))
    assert [e["status"] for e in events] == [
        "START", "RUNNING", "ERROR", "ERROR", "DONE",
    ]
    assert events[-1]["outcome"] == "failed"
    assert "SECRET_VALUE" not in output.getvalue()
    assert "SECRET_VALUE" not in repr(events)
    assert len({e["transaction_ref"] for e in events}) == 1
    # Context state must be released even on exceptions.
    assert step("venv", lambda: 5) == 5
    assert len(events) == 5


def test_listener_failure_does_not_abort_installation_or_lose_terminal_result():
    output = FlushingStream()
    def broken_callback(_event):
        raise RuntimeError("private details")
    with update_operation("source", stream=output, callback=broken_callback):
        assert step("venv", lambda: "ok") == "ok"
    assert "DONE" in output.getvalue()


def test_invalid_method_and_stages_never_pass_untrusted_labels():
    from cifereca.update_progress import UpdateProgress
    with pytest.raises(ValueError):
        UpdateProgress("https://invalid.example/?token=X")
    progress = UpdateProgress("archive")
    with pytest.raises(ValueError):
        progress.emit("../../../users/secret", "RUNNING")
    with pytest.raises(ValueError):
        progress.emit("venv", "secrets")


@pytest.mark.parametrize("method", ["archive", "github", "rollback"])
def test_core_cli_json_stdout_is_single_document_and_progress_stays_on_stderr(
    monkeypatch, capsys, method,
):
    if method == "archive":
        monkeypatch.setattr(updater, "install_archive", lambda *a, **kw: {"ok": True, "dry_run": True})
        args = ["archive.zip", "--dry-run", "--json"]
    elif method == "github":
        monkeypatch.setattr(updater, "install_github", lambda *a, **kw: {"ok": True, "github_commit": "a" * 40})
        args = ["--github", "main", "--json"]
    else:
        monkeypatch.setattr(updater, "rollback", lambda *a, **kw: {"ok": True, "current": "x"})
        args = ["--rollback", "--json"]
    assert updater.main(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ok"] is True
    assert captured.out.count('"ok"') == 1
    assert "START" in captured.err and "DONE" in captured.err
    assert "success" in captured.err


def test_core_cli_fails_with_failed_done_and_still_single_json(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise updater.UpdateError("offline")
    monkeypatch.setattr(updater, "install_github", fail)
    assert updater.main(["--github", "main", "--json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ok"] is False
    assert "ERROR" in captured.err and "outcome=failed" in captured.err


@pytest.mark.parametrize("strict", [False, True])
def test_dev_cli_json_stdout_progress_without_installing_plugins(
    monkeypatch, capsys, strict,
):
    calls = []
    def fake_snapshot(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True, "plugins": [], "dry_run": True}
    monkeypatch.setattr(dev_updater, "apply_snapshot", fake_snapshot)
    args = ["snapshot.zip", "--json", "--dry-run"]
    if strict:
        args += ["--require-plugins-only", "--expected-sha256", "a" * 64]
    assert dev_updater.main(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["dry_run"] is True
    assert "START" in captured.err and "DONE" in captured.err
    assert "dev_plugins_only" in captured.err if strict else "dev_snapshot" in captured.err
    assert calls[0][1]["dry_run"] is True


def test_github_commitish_steps_no_private_subprocess_output(monkeypatch, tmp_path):
    calls = []
    private = "https://repo.invalid/?token=SENSITIVE"
    monkeypatch.setattr(updater.shutil, "which", lambda name: "/bin/" + name)
    def fake_run(command, **kw):
        calls.append(command)
        if "api" in command:
            return NS(returncode=0, stdout="a" * 40 + "\n", stderr=private)
        return NS(returncode=0, stdout="", stderr=private)
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    output = FlushingStream()
    events = []
    with update_operation("github", stream=output, callback=events.append):
        sha = updater._github_checkout("pzia/cifereca", "main", tmp_path)
    assert sha == "a" * 40
    assert len(calls) == 4
    assert [e["stage"] for e in events if e["status"] == "RUNNING"] == [
        "resolve_github", "clone", "fetch", "checkout",
    ]
    assert "SENSITIVE" not in output.getvalue()
    assert "SENSITIVE" not in repr(events)


def test_install_source_dry_run_reports_checks_and_does_not_mutate(monkeypatch, tmp_path):
    release = tmp_path / "source"
    release.mkdir()
    monkeypatch.setattr(updater, "_metadata", lambda src: ("cifereca", "0.9.44"))
    monkeypatch.setattr(updater, "select_python", lambda py=None: "/bin/python")
    monkeypatch.setattr(updater, "install_root", lambda: tmp_path / "installed")
    monkeypatch.setattr(updater, "_copy_source", lambda *a: pytest.fail("copy in dry-run"))
    monkeypatch.setattr(updater, "_create_venv", lambda *a: pytest.fail("venv in dry-run"))
    stream, events = FlushingStream(), []
    with update_operation("source", stream=stream, callback=events.append):
        result = updater.install_source(release, dry_run=True)
    assert result["dry_run"] is True
    assert [e["stage"] for e in events if e["status"] == "RUNNING"] == [
        "metadata", "python_selection",
    ]
    assert events[-1]["outcome"] == "success"
    assert not (tmp_path / "installed").exists()


def test_github_failed_subprocess_marks_error_without_exposing_private_stderr(
    monkeypatch, tmp_path,
):
    private = "https://private.invalid/?auth=DO_NOT_LOG"
    monkeypatch.setattr(updater.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(
        updater.subprocess, "run",
        lambda *_a, **_kw: NS(returncode=1, stdout="", stderr=private),
    )
    stream, events = FlushingStream(), []
    with pytest.raises(updater.UpdateError, match="introuvable"):
        with update_operation("github", stream=stream, callback=events.append):
            updater._github_checkout("pzia/cifereca", "main", tmp_path)
    assert [event["stage"] for event in events if event["status"] == "ERROR"] == [
        "resolve_github", "operation",
    ]
    assert not any(event["stage"] == "clone" for event in events)
    assert events[-1]["outcome"] == "failed"
    assert "DO_NOT_LOG" not in stream.getvalue()
    assert "DO_NOT_LOG" not in repr(events)


def test_long_running_stage_emits_periodic_running_without_fabricated_progress():
    import time

    events, output = [], FlushingStream()
    with update_operation(
        "github", stream=output, callback=events.append,
        heartbeat_seconds=0.008,
    ):
        with update_phase("clone"):
            time.sleep(0.045)
    clone_events = [event for event in events if event["stage"] == "clone"]
    assert len(clone_events) >= 4
    assert clone_events[0]["status"] == "RUNNING"
    assert clone_events[-1]["status"] == "OK"
    assert all(event["status"] == "RUNNING" for event in clone_events[:-1])
    assert all(event["duration_ms"] >= 0 for event in clone_events)
    assert all("percent" not in event for event in events)
    assert events[-1]["status"] == "DONE"
    assert output.flushes == len(events)
    assert output.getvalue().count("github/clone RUNNING") >= 2


def test_heartbeat_tracks_innermost_active_phase_and_resumes_parent():
    import time

    events = []
    with update_operation("dev_snapshot", callback=events.append,
                          heartbeat_seconds=0.007):
        with update_phase("core_activation"):
            with update_phase("venv"):
                time.sleep(0.025)
            time.sleep(0.025)
    phases = [event["stage"] for event in events
              if event["status"] == "RUNNING"]
    assert phases.count("venv") >= 2
    assert phases.count("core_activation") >= 2
    assert events[-1]["status"] == "DONE"
    for phase in ("venv", "core_activation"):
        indices = [index for index, event in enumerate(events)
                   if event["stage"] == phase]
        assert events[indices[-1]]["status"] == "OK"


def test_no_heartbeat_or_thread_when_operation_has_no_observer(monkeypatch):
    from cifereca import update_progress

    def no_thread(*_args, **_kwargs):
        raise AssertionError("thread should never be started without sink")

    monkeypatch.setattr(update_progress, "Thread", no_thread)
    with update_operation("source", heartbeat_seconds=0.005):
        assert step("metadata", lambda: 7) == 7


def test_heartbeat_stops_on_error_without_late_progress_event():
    import time

    events = []
    with pytest.raises(RuntimeError):
        with update_operation("archive", callback=events.append,
                              heartbeat_seconds=0.006):
            with update_phase("snapshot_extract"):
                time.sleep(0.018)
                raise RuntimeError("SECRET_FILE_NAME")
    at_end = len(events)
    time.sleep(0.03)
    assert len(events) == at_end
    assert [e["status"] for e in events[-3:]] == [
        "ERROR", "ERROR", "DONE",
    ]
    assert "SECRET_FILE_NAME" not in repr(events)
