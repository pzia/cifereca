"""#925: optional application launch has a truthful, redacted progress phase."""
from __future__ import annotations

import io
from unittest.mock import Mock

import pytest

from cifereca import dev_updater
from cifereca.update_progress import update_operation
from tests.test_core_p0_925_dev_plugin_ordinals import _snapshot


@pytest.mark.parametrize("fails", [False, True])
def test_restart_launch_has_exactly_one_terminal_phase_without_private_error(
    tmp_path, monkeypatch, fails,
):
    snapshot = _snapshot(tmp_path, monkeypatch)
    monkeypatch.setattr(dev_updater, "_install_plugin_replace", lambda *a, **kw: None)
    launch = Mock(side_effect=OSError("PRIVATE-TOKEN /home/private/runtime") if fails else None)
    monkeypatch.setattr(dev_updater.subprocess, "Popen", launch)
    events, output = [], io.StringIO()
    with update_operation("dev_snapshot", callback=events.append, stream=output):
        result = dev_updater.apply_snapshot(snapshot, restart=True)
    assert result["ok"] is True  # optional restart never undoes the installed update
    assert result["restarted"] is (not fails)
    if fails:
        assert result["restart_error"] == "redemarrage_impossible"
    launch.assert_called_once()
    args, kwargs = launch.call_args
    assert len(args[0]) == 1
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] == kwargs["stderr"] == dev_updater.subprocess.DEVNULL
    phase = [event for event in events if event["stage"] == "restart"]
    assert [event["status"] for event in phase] == ["RUNNING", "ERROR" if fails else "OK"]
    if fails:
        assert phase[-1]["reason_code"] == "internal_error"
    assert events[-1]["outcome"] == "success"
    assert "PRIVATE" not in repr(events) + output.getvalue()
    assert "/home/private" not in repr(events) + output.getvalue()


@pytest.mark.parametrize("dry_run,restart", [(False, False), (True, True)])
def test_unrequested_or_dry_run_restart_never_launches(tmp_path, monkeypatch, dry_run, restart):
    snapshot = _snapshot(tmp_path, monkeypatch)
    monkeypatch.setattr(dev_updater, "_install_plugin_replace", lambda *a, **kw: None)
    launch = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr(dev_updater.subprocess, "Popen", launch)
    events = []
    with update_operation("dev_snapshot", callback=events.append):
        result = dev_updater.apply_snapshot(snapshot, restart=restart, dry_run=dry_run)
    assert result["ok"] is True
    launch.assert_not_called()
    phase = [event for event in events if event["stage"] == "restart"]
    assert [event["status"] for event in phase] == (["SKIPPED"] if dry_run else [])
