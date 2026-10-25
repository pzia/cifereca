"""P0 #925: bounded updater child processes and safe machine-readable timeout outcomes.

All subprocesses are synthetic; no real gh, pip, GTK, plugins or installs.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from cifereca import dev_updater, updater
from cifereca.update_progress import update_operation, update_phase


SECRET = "https://private.invalid/checkout?token=SHOULD_NEVER_LEAK"


def _timeout(command, *, timeout, **_kwargs):
    # TimeoutExpired.__str__ includes its argv, so this represents a private
    # URL, token, and captured output from a failed child.
    raise subprocess.TimeoutExpired(
        ["not-real-command", SECRET], timeout, output=SECRET, stderr=SECRET
    )


def test_safe_process_boundary_suppresses_timeout_command_and_output(monkeypatch):
    monkeypatch.setattr(updater.subprocess, "run", _timeout)
    with pytest.raises(TimeoutError) as error:
        updater._run_bounded_process(["gh", SECRET], timeout=17, check=False)
    assert "Délai dépassé" in str(error.value)
    assert SECRET not in str(error.value)
    assert error.value.__cause__ is None


def test_safe_process_boundary_suppresses_oserror_details(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError(SECRET)
    monkeypatch.setattr(updater.subprocess, "run", unavailable)
    with pytest.raises(updater.UpdateError) as error:
        updater._run_bounded_process([SECRET], timeout=17, check=False)
    assert SECRET not in str(error.value)
    assert error.value.__cause__ is None


def _attested_test_core_wheelhouse(monkeypatch, release):
    """Keep timeout tests past the now-mandatory no-network preflight."""
    from cifereca.wheelhouse_quarantine import attest_wheelhouse
    monkeypatch.setattr(updater, "_metadata", lambda _release: ("cifereca", "0.9.44"))
    wheels = release / "offline-wheels"
    wheels.mkdir()
    (wheels / "cifereca-0.9.44-py3-none-any.whl").write_bytes(b"synthetic-local-core")
    attest_wheelhouse(wheels, core_version="0.9.44")


def test_venv_creation_is_bounded_and_captures_child_output(monkeypatch, tmp_path):
    _attested_test_core_wheelhouse(monkeypatch, tmp_path)
    calls = []
    def timed_out(command, **kwargs):
        calls.append((command, kwargs))
        return _timeout(command, timeout=kwargs["timeout"])
    monkeypatch.setattr(updater.subprocess, "run", timed_out)
    with pytest.raises(TimeoutError):
        updater._create_venv(tmp_path, "/usr/bin/python3")
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 300
    assert calls[0][1]["capture_output"] is True


def test_pip_install_is_bounded_without_network_or_real_venv(monkeypatch, tmp_path):
    _attested_test_core_wheelhouse(monkeypatch, tmp_path)
    calls = []
    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] == ["-m", "venv"]:
            python = tmp_path / ".venv" / ("Scripts/python.exe" if updater.os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True)
            python.touch()
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return _timeout(command, timeout=kwargs["timeout"])
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    with pytest.raises(TimeoutError) as error:
        updater._create_venv(tmp_path, "/usr/bin/python3")
    assert len(calls) == 2
    assert calls[0][1]["timeout"] == 300
    assert calls[1][1]["timeout"] == 900
    assert calls[1][1]["capture_output"] is True
    assert SECRET not in str(error.value)


def test_smoke_timeout_is_closed_failure_with_phase_and_no_raw_data(monkeypatch, tmp_path):
    scripts = tmp_path / ".venv" / ("Scripts" if updater.os.name == "nt" else "bin")
    scripts.mkdir(parents=True)
    for name in updater.PUBLIC_COMMANDS:
        (scripts / (name + (".exe" if updater.os.name == "nt" else ""))).touch()
    monkeypatch.setattr(updater.subprocess, "run", _timeout)
    events = []
    with pytest.raises(TimeoutError):
        with update_operation("archive", callback=events.append):
            with update_phase("smoke"):
                updater._smoke(tmp_path)
    assert [(e["stage"], e["reason_code"]) for e in events if e["status"] == "ERROR"] == [
        ("smoke", "timeout"), ("operation", "timeout"),
    ]
    assert events[-1]["outcome"] == "failed"
    assert SECRET not in repr(events)


def test_github_timeout_still_returns_one_redacted_json_document(monkeypatch, capsys):
    monkeypatch.setattr(updater.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(updater.subprocess, "run", _timeout)
    assert updater.main(["--github", "main", "--json"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "update_error"
    assert "Délai dépassé" in payload["error"]["message"]
    assert captured.out.count('"ok"') == 1
    assert "github/resolve_github ERROR" in captured.err
    assert captured.err.count("reason=timeout") == 2
    assert "outcome=failed" in captured.err
    assert SECRET not in captured.out + captured.err


def test_hostile_timeout_text_never_leaks_from_core_cli(monkeypatch, capsys):
    def fail(*_args, **_kw):
        raise TimeoutError(SECRET)
    monkeypatch.setattr(updater, "install_archive", fail)
    assert updater.main(["snapshot.zip", "--json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "update_error"
    assert SECRET not in captured.out + captured.err


def test_dev_plugin_install_timeout_has_safe_reason_and_does_not_retry(monkeypatch, tmp_path):
    calls = []
    def fake_run(command, *, timeout, on_line):
        calls.append((command, timeout, on_line))
        raise TimeoutError(SECRET)
    monkeypatch.setattr(dev_updater, "run_plugin_child", fake_run)
    monkeypatch.setattr(dev_updater, "_new_cli", lambda: tmp_path / "cli")
    events = []
    with pytest.raises(TimeoutError) as failure:
        with update_operation("dev_snapshot", callback=events.append):
            with update_phase("plugin_install"):
                dev_updater._install_plugin_replace(tmp_path / "plugin.zip", activate=True)
    assert len(calls) == 1
    assert calls[0][1] == dev_updater.PLUGIN_INSTALL_TIMEOUT_SECONDS == 900
    assert callable(calls[0][2])
    assert SECRET not in str(failure.value)
    assert failure.value.__cause__ is None
    assert events[-1]["outcome"] == "failed"
    assert [e["reason_code"] for e in events if e["status"] == "ERROR"] == [
        "timeout", "timeout",
    ]


def test_dev_plugin_install_uses_process_group_runner_and_silences_stderr(monkeypatch, tmp_path):
    calls = []
    def fake_run(command, *, timeout, on_line):
        calls.append((command, timeout))
        on_line(SECRET.encode("utf-8"))
        return SimpleNamespace(returncode=0, stdout='{"ok":true}')
    monkeypatch.setattr(dev_updater, "run_plugin_child", fake_run)
    monkeypatch.setattr(dev_updater, "_new_cli", lambda: tmp_path / "cli")
    dev_updater._install_plugin_replace(tmp_path / "plugin.zip", activate=False)
    assert len(calls) == 1
    assert calls[0][0][-1] == "--no-activate"
    assert calls[0][1] == 900


def test_dev_cli_timeout_remains_redacted_json(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise TimeoutError(SECRET)
    monkeypatch.setattr(dev_updater, "apply_snapshot", fail)
    assert dev_updater.main(["snapshot.zip", "--json"]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["ok"] is False
    assert result["error"]["code"] == "dev_update_error"
    assert SECRET not in captured.out + captured.err
    assert "reason=timeout" in captured.err
    assert "outcome=failed" in captured.err


def test_python_probe_timeout_allows_fallback(monkeypatch):
    monkeypatch.setattr(updater.subprocess, "run", _timeout)
    assert updater._python_version("/fake/python3") is None
