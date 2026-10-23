"""Integrity scan has its own closed GTK-independent progress line (#2804)."""
from __future__ import annotations

from cifereca.gui.maintenance_line_progress import MaintenanceLineProgress
from tests.test_maintenance_cache_row_callbacks import mounted, flush


def test_integrity_line_produces_real_fraction_before_confirmation():
    m = mounted("library.integrity.audit")
    def progress(_ctx, control):
        control.report(message="PRIVATE_LOCAL_PATH")  # private prose ignored
        control.report(current=4, total=18, message="PRIVATE_ACCOUNT")
        return m.tool.plan(_ctx)
    m.tool.plan_with_progress = progress
    m.preview.click()
    args, _ = m.jobs[-1]
    result = args[1]()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert m.feedback.state.phase == "analysis"
    assert bar.visible and bar.fraction == 0.22
    assert label.text.startswith("Vérification d’intégrité en cours")
    assert "PRIVATE_" not in label.text
    assert not spinner.visible
    args[2](result)
    flush(m)
    assert m.feedback.state.phase == "confirmation"
    assert not bar.visible


def test_integrity_line_suppresses_stale_generation_after_refresh():
    queued, output = [], []
    relay = MaintenanceLineProgress(
        idle_add=queued.append, render=output.append,
        activity_label="Vérification d’intégrité",
    )
    first = relay.begin()
    relay.report(first, current=7, total=18)
    relay.begin()
    relay.report(first, current=18, total=18)
    queued.pop(0)()
    assert output[-1].phase == "analysis"
    assert output[-1].fraction is None
