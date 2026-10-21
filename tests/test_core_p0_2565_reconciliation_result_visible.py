"""#2565 — two-click reconciliation must always show a usable local result.

Runs the production MainWindow Maintenance callbacks in the existing inert GTK
harness. No live filesystem scan, providers, database mutation or private data.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.plugin_api import MaintenanceResult
from cifereca.gui.maintenance_line_progress import MaintenanceLineProgress
from tests.test_maintenance_cache_row_callbacks import mounted, execute, flush
from tests import test_core_ui_1358_maintenance_preview_cancellation as harness


def test_reconciliation_plan_is_explained_and_execution_has_distinct_task_kind():
    m = mounted("library.reconciliation.audit")
    assert m.preview.label == "Préparer l’analyse…"
    m.preview.click()
    plan_args, plan_kwargs = m.jobs[-1]
    assert plan_kwargs["kind"] == "maintenance-plan"
    plan_args[2](plan_args[1]())
    preview = harness.Dialog.shown[-1]
    text = preview.extra_child.children[0].text
    assert "L’analyse n’a pas encore commencé." in text
    assert "Exécuter" in text
    assert not any(kwargs["kind"] == "maintenance-tool" for _, kwargs in m.jobs)
    preview.handlers["response"](preview, "run")
    assert m.jobs[-1][1]["kind"] == "maintenance-tool"
    flush(m)
    assert m.status.children[0].text == "Réconciliation en cours…"


def test_failed_snapshot_is_immediately_visible_with_closed_reason_only():
    m = mounted("library.reconciliation.audit")
    args = execute(m)
    secret = "LOCAL_PRIVATE_PATH_DO_NOT_RENDER"
    # Return an incomplete/offline result, not a TaskRunner exception: the
    # existing Core broker and maintenance plugin use this contract on WAL
    # failure, and users must be able to inspect it even when ok=False.
    args[2](MaintenanceResult(
        ok=False,
        summary="Réconciliation indisponible : preuves locales non vérifiables.",
        warnings=("Durée maximale de lecture de la base atteinte.",),
        facts={"secret": secret, "incomplete_scan": 1},
    ))
    flush(m)
    view = m.host._maintenance_result_views[0]
    assert view.button.visible and view.dialog is not None
    assert view.dialog in harness.Dialog.shown
    label = view.dialog.extra_child.children[0].text
    assert "Durée maximale de lecture de la base atteinte." in label
    assert secret not in label
    assert m.feedback.state.phase == "error"
    assert m.activity and m.activity[-1][1]["success"] is False


def test_result_does_not_open_after_identity_generation_change():
    m = mounted("library.reconciliation.audit")
    args = execute(m)
    m.host._identity_context_generation += 1
    before = len(harness.Dialog.shown)
    args[2](MaintenanceResult(True, summary="Do not show"))
    assert len(harness.Dialog.shown) == before
    assert not m.host._maintenance_result_views[0].text


def test_reconciliation_label_is_closed_allowlist_value():
    pending, rendered = [], []
    line = MaintenanceLineProgress(
        idle_add=pending.append,
        render=rendered.append,
        activity_label="Réconciliation",
    )
    ticket = line.begin()
    line.confirmation(ticket)
    line.running(ticket)
    pending.pop(0)()
    assert rendered[-1].label == "Réconciliation en cours…"
    line.report(ticket, message="/PRIVATE/NAME", current=4, total=10)
    pending.pop(0)()
    assert rendered[-1].label == "Réconciliation en cours · 40 %"
    assert "/PRIVATE" not in rendered[-1].label


def test_trace_kind_allowlist_remains_closed_without_private_plugin_labels():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/task_runner.py").read_text(encoding="utf-8")
    assert '"maintenance-plan", "maintenance-tool"' in source
    assert 'else "other"' in source
