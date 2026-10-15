"""#1494: language-audit tool owns its independent, safe GTK progress line."""
from types import SimpleNamespace as NS

import pytest

from tests import test_core_ui_1358_maintenance_preview_cancellation as harness
from tests.test_maintenance_cache_row_callbacks import execute, flush, mounted


def languages():
    return mounted("languages.audit")


def test_language_audit_preview_is_indeterminate_and_reports_only_valid_run_counts():
    m = languages()
    m.preview.click()
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Analyse…"
    assert spinner.visible and spinner.running and not bar.visible
    args, _ = m.jobs[-1]
    args[2](args[1]())
    flush(m)
    assert label.text == "Confirmation attendue"
    dialog = harness.Dialog.shown[-1]
    dialog.handlers["response"](dialog, "run")
    args, _kwargs = m.jobs[-1]
    flush(m)
    assert label.text == "Vérification des langues en cours…"
    assert spinner.visible and not bar.visible
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=2, total=8, message="private/path?token=SECRET")
    flush(m)
    assert label.text == "Vérification des langues en cours · 25 %"
    assert bar.visible and bar.fraction == .25
    assert "SECRET" not in label.text
    control.report(current=8, total=8)
    flush(m)
    assert label.text.endswith("99 %")
    args[2](NS(ok=True, summary="Audit terminé"))
    flush(m)
    assert label.text == "Terminé · 100 %"


@pytest.mark.parametrize("current,total", [(None, None), (0, 0), (True, 5), (-1, 5), (7, 5)])
def test_language_audit_invalid_progress_never_claims_percent(current, total):
    m = languages()
    execute(m)
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=current, total=total, message="PRIVATE")
    flush(m)
    label, bars = m.status.children
    spinner, bar = bars.children
    assert label.text == "Vérification des langues en cours…"
    assert spinner.visible and not bar.visible


def test_language_audit_cancel_and_refresh_ignore_late_worker_callbacks():
    m = languages()
    args = execute(m)
    m.cancel.click()
    args[2](NS(ok=True, summary="stale"))
    flush(m)
    assert m.feedback.state.phase == "cancelled"
    assert m.activity == []
    m2 = languages()
    args = execute(m2)
    old_control = m2.host._maintenance_line_rows[0][1]["control"]
    m2.refresh()
    assert old_control.cancelled()
    args[2](NS(ok=True, summary="stale"))
    flush(m2)
    assert m2.activity == []


@pytest.mark.parametrize("stage,expected", [
    ("preview", "Analyse des langues impossible."),
    ("run", "Vérification des langues impossible."),
])
def test_language_audit_sanitizes_worker_exceptions(stage, expected):
    m = languages()
    def poisoned(*_args):
        raise ValueError("https://private.invalid/?token=SECRET")
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
