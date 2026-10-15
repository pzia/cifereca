"""P1 #1493: per-row cover audit progress via the production Tools callbacks.

The fixture runs the actual MainWindow adapter with inert GTK widgets; no
provider network, filesystem mutation or invented progress fraction is used.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace as NS

from cifereca.gui.maintenance_line_progress import MaintenanceLineProgress
from tests import test_maintenance_cache_row_callbacks as harness


def mounted():
    return harness.mounted(tool_id="covers.audit", danger_level="read-only")


def test_cover_row_is_present_idle_and_indeterminate_during_plan():
    m = mounted()
    assert len(m.host._maintenance_line_rows) == 1
    assert m.feedback.state.phase == "idle"
    m.preview.click()
    harness.flush(m)
    label, controls = m.status.children
    spinner, bar = controls.children
    assert m.feedback.state.phase == "analysis"
    assert label.text == "Analyse des couvertures…"
    assert spinner.visible and spinner.running
    assert not bar.visible
    assert len(m.jobs) == 1
    assert m.jobs[0][1]["kind"] == "maintenance-plan"


def test_cover_audit_confirmation_run_and_success_do_not_invent_counts():
    m = mounted()
    dialog = harness.confirmation(m)
    harness.flush(m)
    assert m.feedback.state.label == "Confirmation attendue"
    dialog.handlers["response"](dialog, "run")
    harness.flush(m)
    args, kwargs = m.jobs[-1]
    label, bars = m.status.children
    spinner, bar = bars.children
    assert kwargs["kind"] == "maintenance-tool"
    assert label.text == "Vérification des couvertures…"
    assert spinner.visible and not bar.visible
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(message="https://private.invalid/?token=VERY_PRIVATE")
    harness.flush(m)
    assert "VERY_PRIVATE" not in label.text
    assert spinner.visible and not bar.visible
    args[2](NS(ok=True, summary="0 couverture manquante"))
    harness.flush(m)
    assert label.text == "Terminé · 100 %"
    assert bar.visible and bar.fraction == 1
    assert not spinner.visible


def test_cover_audit_invalid_and_complete_worker_reports_never_claim_success():
    m = mounted()
    args = harness.execute(m)
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=1, total=0, message="/private/books")
    harness.flush(m)
    assert m.feedback.state.fraction is None
    assert m.feedback.state.label == "Vérification des couvertures…"
    control.report(current=3, total=3, message="unexpected total")
    harness.flush(m)
    assert m.feedback.state.fraction == 0.99
    assert m.feedback.state.label == "Vérification des couvertures · 99 %"
    args[2](NS(ok=False, summary="partial"))
    harness.flush(m)
    assert m.feedback.state.phase == "error"
    assert m.feedback.state.fraction is None


def test_refresh_cancels_coverage_job_and_supresses_late_callbacks():
    m = mounted()
    args = harness.execute(m)
    control = m.host._maintenance_line_rows[0][1]["control"]
    m.refresh()
    assert control.cancelled()
    args[2](NS(ok=True, summary="late"))
    harness.flush(m)
    assert not m.activity and not m.errors
    assert m.host._maintenance_line_rows[0][0] is not m.feedback


def test_other_maintenance_tools_are_not_silently_opted_in():
    m = harness.mounted(tool_id="other.future.tool", danger_level="read-only")
    assert m.host._maintenance_line_rows == []
    assert len(m.host._tools_box.children[0].children[0].children) == 2


@pytest.mark.parametrize("mode", ["https://private", "", "Covers", None, True])
def test_mode_is_fixed_and_never_plugin_arbitrary_text(mode):
    with pytest.raises(ValueError, match="invalid maintenance progress mode"):
        MaintenanceLineProgress(idle_add=lambda _: None, render=lambda _: None, mode=mode)


def test_default_cache_progress_phrasing_is_preserved():
    pending, output = [], []
    progress = MaintenanceLineProgress(idle_add=pending.append, render=output.append)
    ticket = progress.begin()
    progress.confirmation(ticket)
    progress.running(ticket)
    progress.report(ticket, current=1, total=4)
    pending.pop()()
    assert output[-1].label == "Purge en cours · 25 %"


def test_cover_preview_failure_reports_correct_generic_message_without_payload():
    m = mounted()
    secret = "https://private.invalid/?token=VERY_PRIVATE"
    m.tool.plan = lambda _context: (_ for _ in ()).throw(RuntimeError(secret))
    m.preview.click()
    args, _kwargs = m.jobs[-1]
    with pytest.raises(RuntimeError, match="Analyse des couvertures impossible") as caught:
        args[1]()
    assert secret not in str(caught.value)
    assert caught.value.__suppress_context__
    args[3](caught.value)
    harness.flush(m)
    assert m.errors[-1][0] == "Analyse des couvertures impossible."
    assert m.feedback.state.phase == "error"


def test_cover_run_failure_reports_correct_generic_message_without_payload():
    m = mounted()
    args = harness.execute(m)
    secret = "/private/SECRET_COVER_URL"
    m.tool.run = lambda *_args: (_ for _ in ()).throw(RuntimeError(secret))
    with pytest.raises(RuntimeError, match="Vérification des couvertures impossible") as caught:
        args[1]()
    assert secret not in str(caught.value)
    assert caught.value.__suppress_context__
    args[3](caught.value)
    harness.flush(m)
    assert m.errors[-1][0] == "Vérification des couvertures impossible."
    assert m.feedback.state.phase == "error"
