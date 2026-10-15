"""#1498: physical duplicate cleanup has its own safe GTK progress row.

All mutations are simulated through the production MainWindow callback harness;
no physical file is deleted or scheduled for deletion by these tests.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import maintenance_tools as guard
from tests import test_core_ui_1358_maintenance_preview_cancellation as harness
from tests.test_maintenance_cache_row_callbacks import execute, flush, mounted


def duplicates():
    return mounted("library.duplicates.cleanup", danger_level="destructive")


def test_duplicate_scan_is_indeterminate_and_requires_explicit_confirmation():
    m = duplicates()
    m.preview.click()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Analyse…"
    assert spinner.visible and spinner.running and not bar.visible
    args, kw = m.jobs[-1]
    assert kw["kind"] == "maintenance-plan"
    args[2](args[1]())
    flush(m)
    assert label.text == "Confirmation attendue"
    assert not spinner.visible and not bar.visible
    assert len(m.jobs) == 1


def test_duplicate_run_reports_real_pairs_and_never_premature_100_percent():
    m = duplicates()
    args = execute(m)
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Nettoyage des doublons en cours…"
    assert spinner.visible and not bar.visible
    assert m.jobs[-1][1]["kind"] == "maintenance-tool"
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=1, total=2, message="/PRIVATE/sha256?token=SECRET")
    flush(m)
    assert label.text == "Nettoyage des doublons en cours · 50 %"
    assert bar.fraction == .5 and bar.visible
    assert "SECRET" not in label.text
    control.report(current=2, total=2)
    flush(m)
    assert bar.fraction == .99
    args[2](NS(ok=True, summary="Une paire nettoyée"))
    flush(m)
    assert label.text == "Terminé · 100 %"
    assert bar.fraction == 1.0


@pytest.mark.parametrize("current,total", [
    (None, None), (0, 0), (True, 2), (-1, 2), (3, 2), (2, -2),
])
def test_duplicate_scan_does_not_invent_counters(current, total):
    m = duplicates()
    execute(m)
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=current, total=total, message="/PRIVATE")
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Nettoyage des doublons en cours…"
    assert spinner.visible and not bar.visible


def test_duplicate_partial_failure_is_persistent_not_false_success():
    m = duplicates()
    args = execute(m)
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=2, total=2)
    flush(m)
    assert m.feedback.state.fraction == .99
    args[2](NS(ok=False, summary="Suppression partielle"))
    flush(m)
    assert m.feedback.state.phase == "error"
    assert m.status.children[0].text == "Échec"
    assert m.activity[-1][1]["success"] is False
    assert m.activity[-1][1]["persistent"] is True


def test_duplicate_cancel_never_launders_late_worker_success():
    m = duplicates()
    args = execute(m)
    m.cancel.click()
    args[2](NS(ok=True, summary="stale"))
    flush(m)
    assert m.feedback.state.phase == "cancelled"
    assert m.activity == []


def test_duplicate_refresh_disposes_line_and_cancels_old_worker():
    m = duplicates()
    args = execute(m)
    control = m.host._maintenance_line_rows[0][1]["control"]
    m.refresh()
    assert control.cancelled()
    args[2](NS(ok=True, summary="stale"))
    flush(m)
    assert m.activity == []
    assert m.errors == []


def test_duplicate_changed_identity_or_closed_window_never_reopens_dialog():
    for variant in ("identity", "close"):
        m = duplicates()
        m.preview.click()
        args = m.jobs[-1][0]
        plan = args[1]()
        if variant == "identity":
            m.host._identity_context_generation += 1
        else:
            m.host._closed = True
        args[2](plan)
        assert harness.Dialog.shown == []


@pytest.mark.parametrize("stage,expected", [
    ("preview", "Analyse des doublons impossible."),
    ("run", "Nettoyage des doublons impossible."),
])
def test_duplicate_worker_redacts_private_errors_at_task_boundary(stage, expected):
    m = duplicates()
    def poisoned(*_args):
        raise ValueError("https://private.invalid/?token=SECRET private-placement")
    if stage == "preview":
        m.tool.plan = poisoned
        m.preview.click()
        args = m.jobs[-1][0]
    else:
        m.tool.run = poisoned
        args = execute(m)
    with pytest.raises(RuntimeError) as error:
        args[1]()
    assert str(error.value) == expected
    assert error.value.__suppress_context__
    assert "SECRET" not in str(error.value)


def test_duplicate_busy_lock_is_reported_without_private_backend_details():
    m = duplicates()
    m.preview.click()
    args = m.jobs[-1][0]
    args[3](guard.MaintenanceBusyError("/PRIVATE/lock/path"))
    flush(m)
    assert m.feedback.state.phase == "error"
    assert m.errors[-1][0] == guard.BUSY_MESSAGE
    assert "/PRIVATE" not in str(m.errors[-1])
