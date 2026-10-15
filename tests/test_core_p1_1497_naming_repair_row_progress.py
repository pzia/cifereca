"""#1497: naming repair owns an independent CoreUI maintenance progress line.

These tests exercise the real MainWindow callbacks with the existing GTK
harness. They never rename a physical file or bypass the confirmation dialog.
"""
from types import SimpleNamespace as NS

import pytest

from tests import test_core_ui_1358_maintenance_preview_cancellation as harness
from tests.test_maintenance_cache_row_callbacks import execute, flush, mounted


def repair():
    return mounted("library.naming.repair")


def test_rename_plan_is_indeterminate_until_explicit_confirmation():
    m = repair()
    m.preview.click()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Analyse…"
    assert spinner.visible and spinner.running
    assert not bar.visible
    plan_args, plan_kwargs = m.jobs[-1]
    assert plan_kwargs["kind"] == "maintenance-plan"
    plan_args[2](plan_args[1]())
    flush(m)
    assert label.text == "Confirmation attendue"
    assert not spinner.visible and not bar.visible
    assert len(m.jobs) == 1  # no mutation before confirmation


def test_rename_run_shows_only_attested_counts_and_completes_at_callback():
    m = repair()
    args = execute(m)
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Renormalisation des noms en cours…"
    assert spinner.visible and not bar.visible
    assert m.jobs[-1][1]["kind"] == "maintenance-tool"
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=3, total=12, message="/secret/filename?token=PRIVATE")
    flush(m)
    assert label.text == "Renormalisation des noms en cours · 25 %"
    assert bar.visible and bar.fraction == .25
    assert "PRIVATE" not in label.text
    control.report(current=12, total=12)
    flush(m)
    assert label.text.endswith("99 %")
    assert bar.fraction == .99
    args[2](NS(ok=True, summary="Renommages terminés"))
    flush(m)
    assert label.text == "Terminé · 100 %"
    assert bar.fraction == 1


@pytest.mark.parametrize("current,total", [
    (None, None), (0, 0), (False, 3), (-1, 3), (4, 3), (1, -1),
])
def test_rename_unreliable_counts_never_create_percentage(current, total):
    m = repair()
    execute(m)
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=current, total=total, message="PRIVATE")
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Renormalisation des noms en cours…"
    assert spinner.visible and not bar.visible


def test_partial_rename_failure_never_claims_commit_or_rollback():
    m = repair()
    args = execute(m)
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=2, total=2)
    flush(m)
    assert m.feedback.state.label.endswith("99 %")
    args[2](NS(ok=False, summary="Renommage partiel : deux placements ignorés"))
    flush(m)
    assert m.feedback.state.phase == "error"
    assert m.status.children[0].text == "Échec"
    assert m.activity[-1][1]["success"] is False
    assert m.activity[-1][1]["persistent"] is True


def test_rename_cancel_and_refresh_discard_stale_completion():
    m = repair()
    args = execute(m)
    m.cancel.click()
    args[2](NS(ok=True, summary="stale success"))
    flush(m)
    assert m.feedback.state.phase == "cancelled"
    assert m.activity == []
    newer = repair()
    args = execute(newer)
    old_control = newer.host._maintenance_line_rows[0][1]["control"]
    newer.refresh()
    assert old_control.cancelled()
    args[2](NS(ok=True, summary="stale"))
    flush(newer)
    assert newer.activity == []


def test_rename_identity_change_blocks_outdated_plan_dialog():
    m = repair()
    m.preview.click()
    args, _ = m.jobs[-1]
    plan = args[1]()
    m.host._identity_context_generation += 1
    args[2](plan)
    assert harness.Dialog.shown == []
    assert m.feedback.state.phase == "analysis"


def test_rename_closed_window_drops_old_result():
    m = repair()
    args = execute(m)
    m.host._closed = True
    args[2](NS(ok=True, summary="stale"))
    flush(m)
    assert m.activity == []


@pytest.mark.parametrize("stage,expected", [
    ("preview", "Analyse des renommages impossible."),
    ("run", "Renormalisation des noms impossible."),
])
def test_rename_worker_redacts_private_errors_before_runner(stage, expected):
    m = repair()
    def poisoned(*_args):
        raise RuntimeError("https://private.invalid/?token=SECRET /var/private")
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
