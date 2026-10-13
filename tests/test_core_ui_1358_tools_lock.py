from __future__ import annotations

from dataclasses import dataclass

import pytest

from cifereca.gui import maintenance_tools as guard
from cifereca.locking import InterProcessLock


@dataclass(frozen=True)
class Plan:
    tool_id: str = "library.duplicates.cleanup"
    summary: str = "Plan de nettoyage initial"
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

def test_mutated_reused_plan_cannot_silently_change_confirmed_action(tmp_path, monkeypatch):
    """A frozen dataclass may still contain a mutable facts mapping."""
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "shared.lock")
    original = Plan(facts={"pairs": [["copy-1", "keep-1"]]})
    tool = Tool(original)

    preview = guard.preview_maintenance_tool(tool, object(), destructive=True)
    assert preview is not original
    assert preview.facts == original.facts

    # The plugin reuses the same plan object and mutates a nested list after
    # GTK displayed the preview. This must be caught before any deletion.
    original.facts["pairs"][0][0] = "copy-2"
    assert preview.facts["pairs"][0][0] == "copy-1"
    with pytest.raises(guard.MaintenancePlanChangedError, match="Prévisualiser"):
        guard.execute_maintenance_tool(tool, object(), preview, Control(), destructive=True)
    assert tool.run_calls == 0



def test_plan_summary_change_refuses_destructive_execution(tmp_path, monkeypatch):
    """A changed user-visible summary is a stale plan, even with same facts."""
    from dataclasses import replace

    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "guard.lock")
    tool = Tool()
    reviewed = guard.preview_maintenance_tool(tool, object(), destructive=True)
    tool.preview = replace(reviewed, summary="Des suppressions supplémentaires sont proposées")
    with pytest.raises(guard.MaintenancePlanChangedError, match="Prévisualiser"):
        guard.execute_maintenance_tool(tool, object(), reviewed, Control(), destructive=True)
    assert tool.run_calls == 0


def test_cancel_requested_before_destructive_run_keeps_library_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "guard.lock")
    tool = Tool()
    reviewed = guard.preview_maintenance_tool(tool, object(), destructive=True)
    ctl = guard.MaintenanceCancelControl()
    ctl.cancel()
    assert ctl.cancelled()
    with pytest.raises(guard.MaintenanceCancelledError, match="annulée"):
        guard.execute_maintenance_tool(tool, object(), reviewed, ctl, destructive=True)
    assert tool.plan_calls == 1
    assert tool.run_calls == 0


def test_cancel_during_cancellable_plugin_stops_following_mutations(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "guard.lock")
    tool = Tool()
    reviewed = guard.preview_maintenance_tool(tool, object(), destructive=True)
    ctl = guard.MaintenanceCancelControl()
    def partial_then_cancel(_plan, control):
        control.check_cancelled()
        ctl.cancel()
        control.check_cancelled()
        pytest.fail("no further mutation after cancellation")
    tool.run = partial_then_cancel
    with pytest.raises(guard.MaintenanceCancelledError):
        guard.execute_maintenance_tool(tool, object(), reviewed, ctl, destructive=True)


def test_window_close_terminates_maintenance_control():
    state = {"closed": False}
    ctl = guard.MaintenanceCancelControl(owner_closed=lambda: state["closed"])
    assert not ctl.cancelled()
    state["closed"] = True
    with pytest.raises(guard.MaintenanceCancelledError):
        ctl.check_cancelled()


def test_preview_cancellation_checked_before_plugin_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "guard.lock")
    for destructive in (False, True):
        tool = Tool()
        control = guard.MaintenanceCancelControl()
        control.cancel()
        with pytest.raises(guard.MaintenanceCancelledError):
            guard.preview_maintenance_tool(
                tool, object(), destructive=destructive, control=control,
            )
        assert tool.plan_calls == 0


def test_preview_cancel_during_scan_suppresses_result_even_without_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "guard.lock")
    for destructive in (False, True):
        tool = Tool()
        control = guard.MaintenanceCancelControl()
        planned = tool.plan

        def cancel_mid_scan(context):
            outcome = planned(context)
            control.cancel()
            return outcome

        tool.plan = cancel_mid_scan
        with pytest.raises(guard.MaintenanceCancelledError):
            guard.preview_maintenance_tool(
                tool, object(), destructive=destructive, control=control,
            )
        assert tool.plan_calls == 1


def test_preview_non_cancelled_still_returns_original_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "acquisition_lock_file", lambda: tmp_path / "guard.lock")
    tool = Tool()
    ctl = guard.MaintenanceCancelControl()
    for destructive in (False, True):
        plan = guard.preview_maintenance_tool(
            tool, object(), destructive=destructive, control=ctl,
        )
        assert plan == tool.preview
        if destructive:
            assert plan is not tool.preview
