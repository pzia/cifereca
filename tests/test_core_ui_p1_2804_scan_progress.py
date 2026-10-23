"""#2804 live planning progress; legacy plugins remain compatible."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.maintenance_line_progress import MaintenanceLineProgress
from cifereca.gui.maintenance_tools import (
    MaintenanceCancelControl, MaintenanceCancelledError,
    preview_maintenance_tool,
)
from tests.test_maintenance_cache_row_callbacks import mounted, flush


class Legacy:
    def __init__(self):
        self.calls = []

    def plan(self, context):
        self.calls.append(context)
        return {"old_plan": True}


class Progressive(Legacy):
    def __init__(self):
        super().__init__()
        self.seen = []

    def plan_with_progress(self, context, control):
        self.seen.append(context)
        control.report(current=0, total=18, message="PRIVATE_PATH")
        control.check_cancelled()
        control.report(current=9, total=18, message="PRIVATE_BOOK")
        return {"progressive": True}


def test_optional_progress_is_explicit_and_legacy_plan_unmodified():
    context = NS(identity_id="first")
    legacy = Legacy()
    sink = []
    control = MaintenanceCancelControl(on_progress=lambda *v: sink.append(v))
    assert preview_maintenance_tool(
        legacy, context, destructive=False, control=control,
    ) == {"old_plan": True}
    assert legacy.calls == [context]
    assert sink == []

    producer = Progressive()
    assert preview_maintenance_tool(
        producer, context, destructive=False, control=control,
    ) == {"progressive": True}
    assert producer.seen == [context]
    assert producer.calls == []
    assert sink == [(0, 18, "PRIVATE_PATH"), (9, 18, "PRIVATE_BOOK")]


def test_plugin_typeerror_is_not_swallowed_as_legacy_fallback():
    producer = Progressive()
    def broken(_context, _control):
        raise TypeError("broken plugin, not a contract fallback")
    producer.plan_with_progress = broken
    with pytest.raises(TypeError, match="broken plugin"):
        preview_maintenance_tool(
            producer, NS(), destructive=False, control=MaintenanceCancelControl(),
        )
    assert producer.calls == []


def test_cancelled_progress_does_not_publish_a_plan():
    producer = Progressive()
    control = MaintenanceCancelControl()
    control.cancel()
    with pytest.raises(MaintenanceCancelledError):
        preview_maintenance_tool(producer, NS(), destructive=False, control=control)
    assert producer.calls == [] and producer.seen == []


def test_progress_on_analyzing_line_keeps_confirmation_transition_and_coalesces():
    queued, rendered = [], []
    line = MaintenanceLineProgress(
        idle_add=queued.append, render=rendered.append,
        activity_label="Vérification des couvertures",
    )
    ticket = line.begin()
    for count in range(18):
        line.report(ticket, current=count, total=18, message="PRIVATE_PATH")
    assert len(queued) == 1
    queued.pop(0)()
    assert rendered[-1].phase == "analysis"
    assert rendered[-1].fraction == 0.94  # integer percentage, capped below 100%
    assert "PRIVATE_PATH" not in rendered[-1].label
    line.confirmation(ticket)
    queued.pop(0)()
    assert rendered[-1].phase == "confirmation"
    assert rendered[-1].fraction is None
    line.running(ticket)
    line.report(ticket, current=10, total=10)
    queued.pop(0)()
    assert rendered[-1].phase == "running"
    assert rendered[-1].fraction == 0.99
    line.finish(ticket, "success")
    queued.pop(0)()
    assert rendered[-1].fraction == 1


def test_live_previews_in_real_tools_callback_harness_are_non_modal():
    # Exercise the actual MainWindow._refresh_tools_page callback assembly
    # without GTK4 installed on the generic Core CI runner.
    m = mounted("covers.audit")
    def live_plan(_ctx, control):
        control.report(current=0, total=18, message="/PRIVATE/path")
        control.report(current=9, total=18, message="ACCOUNT_SECRET")
        return m.tool.plan(_ctx)
    m.tool.plan_with_progress = live_plan
    m.preview.click()
    args, kwargs = m.jobs[-1]
    result = args[1]()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert m.feedback.state.phase == "analysis"
    assert bar.visible and bar.fraction == 0.5
    assert "PRIVATE" not in label.text
    assert not spinner.visible
    assert len(m.jobs) == 1
    args[2](result)
    flush(m)
    assert m.feedback.state.phase == "confirmation"
    assert not bar.visible


@pytest.mark.parametrize("amount,total", [(0, 18), (9, 18), (600, 600)])
def test_future_maintenance_tool_gets_a_real_private_safe_preview_line(amount, total):
    """No CoreUI ID registration required for an installed future tool."""
    m = mounted("future.library.custom.audit")

    def plan(_context, control):
        control.report(current=amount, total=total,
                       message="PRIVATE_SECRET_ACCOUNT/path/to/book.epub")
        return m.tool.plan(_context)

    m.tool.plan_with_progress = plan
    m.preview.click()
    args, kwargs = m.jobs[-1]
    result = args[1]()  # real production callback inside worker harness
    flush(m)
    label, bars = m.status.children
    spinner, progress = bars.children
    assert m.feedback.state.phase == "analysis"
    assert label.text.startswith("Traitement en cours")
    assert "PRIVATE_" not in label.text and "book.epub" not in label.text
    assert progress.visible and not spinner.visible
    assert progress.fraction == min(99, (amount * 100) // total) / 100
    args[2](result)
    flush(m)
    assert m.feedback.state.phase == "confirmation"
    assert not progress.visible
    # Selection/confirmation remains required; progress is not authorization.
    assert not any(k.get("kind") == "maintenance-run" for _, k in m.jobs)


def test_legacy_future_maintenance_tool_has_honest_indeterminate_progress():
    m = mounted("future.library.legacy.audit")
    m.preview.click()
    flush(m)
    label, bars = m.status.children
    spinner, progress = bars.children
    assert label.text == "Analyse…"
    assert spinner.visible and not progress.visible
    args, _ = m.jobs[-1]
    plan = args[1]()
    args[2](plan)
    flush(m)
    assert m.feedback.state.phase == "confirmation"
    assert not progress.visible
    assert not spinner.visible


def test_future_tool_ignores_late_private_updates_after_row_refresh():
    m = mounted("future.library.bibliography.audit")
    def plan(_context, control):
        control.report(current=1, total=18, message="PRIVATE_SECRET")
        return m.tool.plan(_context)
    m.tool.plan_with_progress = plan
    m.preview.click()
    args, _ = m.jobs[-1]
    result = args[1]()
    original_control = m.host._maintenance_line_rows[0][1]["control"]
    m.refresh()
    assert original_control.cancelled()
    args[2](result)
    flush(m)
    assert m.feedback.state.phase == "analysis"
    assert m.feedback.valid(1) is False
    assert m.activity == []
