"""#1494: a plugin language-audit row uses the existing CoreUI progress relay."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.maintenance_line_progress import MaintenanceLineProgress
from tests.test_maintenance_cache_row_callbacks import (
    confirmation, execute, flush, mounted,
)


def _widgets(m):
    label, bars = m.status.children
    spinner, bar = bars.children
    return label, spinner, bar


def test_language_audit_renders_indeterminate_scan_confirmation_and_completion():
    m = mounted("languages.audit")
    m.preview.click()
    flush(m)
    label, spinner, bar = _widgets(m)
    assert label.text == "Analyse…"
    assert spinner.running and spinner.visible
    assert not bar.visible
    args, _ = m.jobs[-1]
    args[2](args[1]())
    flush(m)
    assert label.text == "Confirmation attendue"
    dialog = __import__(
        "tests.test_core_ui_1358_maintenance_preview_cancellation",
        fromlist=["Dialog"],
    ).Dialog.shown[-1]
    dialog.handlers["response"](dialog, "run")
    flush(m)
    assert label.text == "Vérification des langues en cours…"
    assert spinner.visible and not bar.visible

    worker_args, _ = m.jobs[-1]
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(message="/home/reader/private.epub Bearer PRIVATE_TOKEN")
    flush(m)
    assert label.text == "Vérification des langues en cours…"
    assert bar.fraction is None and not bar.visible
    assert "PRIVATE_TOKEN" not in label.text
    worker_args[2](NS(ok=True, summary="Audit achevé"))
    flush(m)
    assert label.text == "Terminé · 100 %"
    assert bar.visible and bar.fraction == 1
    assert len(m.activity) == 1


def test_language_progress_never_claims_100_percent_before_confirmed_success():
    m = mounted("languages.audit")
    args = execute(m)
    control = m.host._maintenance_line_rows[0][1]["control"]
    control.report(current=2, total=8, message="private")
    flush(m)
    label, spinner, bar = _widgets(m)
    assert label.text == "Vérification des langues en cours · 25 %"
    assert bar.fraction == .25 and not spinner.visible
    control.report(current=8, total=8)
    flush(m)
    assert bar.fraction == .99
    args[2](NS(ok=False, summary="Erreur de lecture"))
    flush(m)
    assert label.text == "Échec"
    assert not bar.visible


@pytest.mark.parametrize("phase,expected", [
    ("preview", "Analyse des langues impossible."),
    ("run", "Vérification des langues impossible."),
])
def test_language_provider_exception_sanitized_before_global_activity(phase, expected):
    m = mounted("languages.audit")
    secret = "file:///private/credential?bearer=TOP_SECRET"
    def fail(*_args):
        raise RuntimeError(secret)
    if phase == "preview":
        m.tool.plan = fail
        m.preview.click()
        args, _ = m.jobs[-1]
    else:
        m.tool.run = fail
        args = execute(m)
    with pytest.raises(RuntimeError, match="impossible") as caught:
        args[1]()
    assert str(caught.value) == expected
    assert caught.value.__suppress_context__
    assert secret not in str(caught.value)
    args[3](caught.value)
    flush(m)
    assert m.errors[-1][0] == expected
    assert m.feedback.state.phase == "error"


def test_refresh_and_identity_change_drop_stale_language_callbacks():
    m = mounted("languages.audit")
    m.preview.click()
    args, _ = m.jobs[-1]
    plan = args[1]()
    m.host._identity_context_generation += 1
    args[2](plan)
    assert m.feedback.state.phase != "confirmation"
    m.refresh()
    assert not m.feedback.valid(1)
    args[2](plan)
    flush(m)
    assert not m.activity and not m.errors


def test_languages_name_is_core_constant_not_plugin_report_text():
    callbacks, states = [], []
    p = MaintenanceLineProgress(
        idle_add=callbacks.append, render=states.append,
        activity_label="Vérification des langues",
    )
    ticket = p.begin()
    p.confirmation(ticket)
    p.running(ticket)
    p.report(ticket, message="Authorization: Bearer PRIVATE_TOKEN")
    callbacks.pop()()
    assert states[-1].label == "Vérification des langues en cours…"
    assert "PRIVATE_TOKEN" not in states[-1].label
    p.dispose()


def test_other_maintenance_tool_is_not_silently_enrolled_in_this_ticket():
    m = mounted("library.integrity.audit")
    assert m.host._maintenance_line_rows == []
