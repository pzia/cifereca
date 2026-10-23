"""#2804: destructive confirmation keeps same lock while replan reports progress."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.gui import maintenance_tools as guard
from cifereca.locking import InterProcessLock
from tests.test_core_ui_1358_tools_lock import Tool, Plan


class Progressive(Tool):
    def __init__(self):
        super().__init__()
        self.progress_calls = 0
        self.on_replan = None

    def plan_with_progress(self, context, control):
        self.progress_calls += 1
        control.report(current=0, total=18, message="PRIVATE_PATH")
        for n in range(1, 19):
            control.check_cancelled()
            if n == 16:
                control.report(current=15, total=18, message="PRIVATE_ACCOUNT")
        if self.on_replan is not None:
            self.on_replan()
        return self.preview


def test_revalidation_progress_does_not_release_same_mutation_lock(tmp_path, monkeypatch):
    lock = tmp_path / "guard.lock"
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: lock)
    tool = Progressive()
    # Record the exact, user-reviewed immutable preview.
    reviewed = guard.preview_maintenance_tool(
        tool, object(), destructive=True,
        control=guard.MaintenanceCancelControl(),
    )
    assert tool.progress_calls == 1
    stages = []
    control = guard.MaintenanceCancelControl(
        on_progress=lambda current, total, message:
            stages.append((current, total, message)),
    )
    def verify_lock():
        with pytest.raises(guard.LockBusyError):
            with InterProcessLock(lock):
                pass
    tool.on_replan = verify_lock
    result = guard.execute_maintenance_tool(
        tool, object(), reviewed, control, destructive=True,
    )
    assert result == {"ok": True}
    assert tool.progress_calls == 2
    assert tool.run_calls == 1
    assert len(stages) >= 2
    assert stages[0][:2] == (0, 18)
    assert stages[-1][:2] == (15, 18)


def test_stale_replan_rejects_changes_even_with_numerical_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "shared.lock")
    tool = Progressive()
    reviewed = guard.preview_maintenance_tool(
        tool, object(), destructive=True, control=guard.MaintenanceCancelControl(),
    )
    tool.preview = replace(reviewed, changes=("changed-after-confirmation",))
    with pytest.raises(guard.MaintenancePlanChangedError):
        guard.execute_maintenance_tool(
            tool, object(), reviewed, guard.MaintenanceCancelControl(),
            destructive=True,
        )
    assert tool.run_calls == 0


def test_cancel_during_revalidation_prevents_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "shared.lock")
    tool = Progressive()
    reviewed = guard.preview_maintenance_tool(
        tool, object(), destructive=True, control=guard.MaintenanceCancelControl(),
    )
    control = None
    def report(current, _total, _message):
        if current == 0:
            control.cancel()
    control = guard.MaintenanceCancelControl(on_progress=report)
    with pytest.raises(guard.MaintenanceCancelledError):
        guard.execute_maintenance_tool(
            tool, object(), reviewed, control, destructive=True,
        )
    assert tool.run_calls == 0
    assert tool.progress_calls == 2


def test_legacy_destructive_replan_remains_exactly_one_call(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "shared.lock")
    tool = Tool()
    control = guard.MaintenanceCancelControl()
    reviewed = guard.preview_maintenance_tool(
        tool, object(), destructive=True, control=control,
    )
    assert guard.execute_maintenance_tool(
        tool, object(), reviewed, control, destructive=True,
    ) == {"ok": True}
    assert tool.plan_calls == 2 and tool.run_calls == 1
