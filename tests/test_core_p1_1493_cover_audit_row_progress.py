"""#1493: cover-audit Tools row uses the live shared GTK progress contract."""
from types import SimpleNamespace as NS

import pytest

from tests import test_core_ui_1358_maintenance_preview_cancellation as harness
from tests.test_maintenance_cache_row_callbacks import (
    confirmation, execute, flush, mounted,
)


def covers():
    return mounted("covers.audit")


def test_cover_audit_preview_and_run_use_indeterminate_activity_then_real_counts():
    m = covers()
    m.preview.click()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Analyse…"
    assert spinner.visible and spinner.running
    assert not bar.visible
    args, kwargs = m.jobs[-1]
    assert kwargs["kind"] == "maintenance-plan"
    args[2](args[1]())
    assert m.feedback.state.phase == "confirmation"
    flush(m)
    assert label.text == "Confirmation attendue"
    assert not spinner.visible and not bar.visible

    # The real GTK confirmation opens a distinct worker; no percent before
    # the producer reports valid counts.
    dialog = harness.Dialog.shown[-1]
    dialog.handlers["response"](dialog, "run")
    flush(m)
    assert label.text == "Vérification des couvertures en cours…"
    assert spinner.visible and not bar.visible
    run_args, run_kwargs = m.jobs[-1]
    assert run_kwargs["kind"] == "maintenance-tool"
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=1, total=4, message="/PRIVATE/path?token=SECRET")
    flush(m)
    assert "Vérification des couvertures en cours · 25 %" == label.text
    assert bar.visible and bar.fraction == .25
    assert "SECRET" not in label.text
    assert not spinner.visible

    # A finished-but-not-delivered progress report cannot claim success.
    control.report(current=4, total=4)
    flush(m)
    assert bar.fraction == .99
    assert label.text.endswith("99 %")
    run_args[2](NS(ok=True, summary="Audit terminé"))
    flush(m)
    assert label.text == "Terminé · 100 %"
    assert bar.fraction == 1
    assert len(m.activity) == 1


@pytest.mark.parametrize("current,total", [
    (None, None), (0, 0), (True, 5), (-1, 3), (6, 5),
])
def test_cover_audit_does_not_invent_percentages(current, total):
    m = covers()
    execute(m)
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=current, total=total, message="TOKEN_PRIVATE")
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Vérification des couvertures en cours…"
    assert spinner.visible and not bar.visible
    assert "TOKEN_PRIVATE" not in label.text


def test_cover_audit_cancel_stays_cancelled_despite_late_success():
    m = covers()
    args = execute(m)
    m.cancel.click()
    args[2](NS(ok=True, summary="Incorrect"))
    flush(m)
    assert m.feedback.state.phase == "cancelled"
    assert m.activity == []


def test_cover_audit_refresh_cancels_worker_and_discards_stale_callbacks():
    m = covers()
    args = execute(m)
    old_control = m.host._maintenance_line_rows[0][1]["control"]
    m.refresh()
    assert old_control.cancelled()
    args[2](NS(ok=True, summary="Stale"))
    flush(m)
    assert m.activity == []
    assert m.errors == []


def test_cover_audit_scoped_identity_generation_blocks_old_preview():
    m = covers()
    m.preview.click()
    args, _kw = m.jobs[-1]
    plan = args[1]()
    m.host._identity_context_generation += 1
    args[2](plan)
    assert m.feedback.state.phase == "analysis"
    assert m.activity == []


@pytest.mark.parametrize("stage,expected", [
    ("preview", "Analyse des couvertures impossible."),
    ("run", "Vérification des couvertures impossible."),
])
def test_cover_audit_worker_sanitizes_private_provider_failures(stage, expected):
    m = covers()
    def poisoned(*_args):
        raise ValueError("https://private.invalid/?token=SECRET local/path")
    if stage == "preview":
        m.tool.plan = poisoned
        m.preview.click()
        args = m.jobs[-1][0]
    else:
        m.tool.run = poisoned
        args = execute(m)
    with pytest.raises(RuntimeError) as ex:
        args[1]()
    assert str(ex.value) == expected
    assert ex.value.__suppress_context__
    assert "SECRET" not in str(ex.value)


def test_cache_tool_keeps_previous_purge_labels():
    m = mounted()
    args = execute(m)
    flush(m)
    assert m.status.children[0].text == "Purge en cours…"
    args[2](NS(ok=True, summary="Ok"))
    flush(m)
    assert m.status.children[0].text == "Terminé · 100 %"
