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


def test_unregistered_future_tool_gets_private_safe_measured_progress_row():
    """No hard-coded tool ID is needed to render a real progressive preview."""
    m = mounted("plugin.future.collection.scan")
    assert len(m.host._maintenance_line_rows) == 1

    def scan_with_progress(context, control):
        control.report(current=0, total=18, message="PRIVATE/path and SECRET_ACCOUNT")
        control.report(current=9, total=18, message="PRIVATE title")
        return m.tool.plan(context)

    m.tool.plan_with_progress = scan_with_progress
    m.preview.click()
    args, _kwargs = m.jobs[-1]
    assert m.feedback.state.phase == "analysis"
    args[1]()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Traitement en cours · 50 %"
    assert bar.visible and bar.fraction == .5
    assert not spinner.visible
    assert "PRIVATE" not in label.text and "SECRET" not in label.text
    assert len(m.jobs) == 1


def test_unregistered_legacy_tool_keeps_indeterminate_row_without_fake_counts():
    """Legacy plan(context) is valid; lack of counters is not a false 0/1."""
    m = mounted("plugin.old.audit")
    m.preview.click()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Analyse…"
    assert spinner.visible and spinner.running and not bar.visible
    args, _kwargs = m.jobs[-1]
    args[1]()
    flush(m)
    assert m.feedback.state.phase == "analysis"
    assert m.feedback.state.fraction is None


def test_two_unknown_plugin_rows_are_isolated_and_drop_late_refresh_reports():
    a = mounted("plugin.future.A")
    b = mounted("plugin.future.B")
    a.preview.click()
    b.preview.click()
    ta, tb = a.feedback.state, b.feedback.state
    assert ta.phase == tb.phase == "analysis"
    ca = a.host._maintenance_line_rows[0][1]["control"]
    cb = b.host._maintenance_line_rows[0][1]["control"]
    assert ca is not cb
    ca.report(current=3, total=6, message="/PRIVATE/A")
    cb.report(current=1, total=4, message="/PRIVATE/B")
    flush(a)
    flush(b)
    assert a.feedback.state.fraction == .5
    assert b.feedback.state.fraction == .25
    a.refresh()
    assert ca.cancelled()
    cb.report(current=2, total=4, message="PRIVATE_B")
    flush(b)
    assert b.feedback.state.fraction == .5
    assert a.feedback.state.phase == "analysis" or not a.feedback.valid(
        a.host._maintenance_line_rows[0][1]["ticket"] or -1
    )
