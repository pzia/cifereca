from __future__ import annotations

from dataclasses import dataclass

import pytest

from cifereca.gui import maintenance_tools as guard
from cifereca.locking import InterProcessLock


@dataclass(frozen=True)
class Plan:
    tool_id: str = "library.duplicates.cleanup"
    facts: dict | None = None
    changes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class Tool:
    def __init__(self, plan=None):
        self.preview = plan or Plan(facts={"pairs": (("copy", "keep"),)})
        self.plan_calls = 0
        self.run_calls = 0

    def plan(self, _context):
        self.plan_calls += 1
        return self.preview

    def run(self, _plan, control):
        control.check_cancelled()
        self.run_calls += 1
        return {"ok": True}


class Control:
    def check_cancelled(self):
        return None


def test_destructive_preview_rejects_busy_sync_before_io(tmp_path, monkeypatch):
    path = tmp_path / "shared.lock"
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: path)
    tool = Tool()
    with InterProcessLock(path):
        with pytest.raises(guard.MaintenanceBusyError, match="Synchronisation en cours"):
            guard.preview_maintenance_tool(tool, object(), destructive=True)
    assert tool.plan_calls == 0


def test_destructive_execution_refuses_busy_sync_without_mutation(tmp_path, monkeypatch):
    path = tmp_path / "shared.lock"
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: path)
    tool = Tool()
    with InterProcessLock(path):
        with pytest.raises(guard.MaintenanceBusyError):
            guard.execute_maintenance_tool(tool, object(), tool.preview, Control(), destructive=True)
    assert tool.plan_calls == 0
    assert tool.run_calls == 0


def test_destructive_execution_rejects_stale_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "shared.lock")
    tool = Tool()
    preview = guard.preview_maintenance_tool(tool, object(), destructive=True)
    tool.preview = Plan(facts={"pairs": (("other", "keep"),)})
    with pytest.raises(guard.MaintenancePlanChangedError, match="Prévisualiser"):
        guard.execute_maintenance_tool(tool, object(), preview, Control(), destructive=True)
    assert tool.run_calls == 0


def test_destructive_execution_revalidates_and_runs_under_same_lock(tmp_path, monkeypatch):
    path = tmp_path / "shared.lock"
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: path)
    tool = Tool()
    preview = guard.preview_maintenance_tool(tool, object(), destructive=True)
    original_run = tool.run

    def run_with_lock_check(plan, control):
        with pytest.raises(guard.LockBusyError):
            with InterProcessLock(path):
                pass
        return original_run(plan, control)

    tool.run = run_with_lock_check
    result = guard.execute_maintenance_tool(tool, object(), preview, Control(), destructive=True)
    assert result["ok"] is True
    assert tool.plan_calls == 2
    assert tool.run_calls == 1


def test_read_failure_does_not_authorize_destructive_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "shared.lock")
    plan = Plan(facts={"pairs": (("copy", "keep"),), "read_failures": 1})
    tool = Tool(plan)
    with pytest.raises(guard.MaintenancePlanChangedError):
        guard.execute_maintenance_tool(tool, object(), plan, Control(), destructive=True)
    assert tool.run_calls == 0


def test_readonly_preview_and_run_work_even_during_sync(tmp_path, monkeypatch):
    path = tmp_path / "shared.lock"
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: path)
    tool = Tool()
    with InterProcessLock(path):
        plan = guard.preview_maintenance_tool(tool, object(), destructive=False)
        guard.execute_maintenance_tool(tool, object(), plan, Control(), destructive=False)
    assert tool.run_calls == 1
