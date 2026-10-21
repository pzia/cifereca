"""Local results remain usable without exposing execution facts or stale identities."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.maintenance_result_view import result_text
from cifereca.plugin_api import MaintenanceResult
from tests.test_maintenance_cache_row_callbacks import mounted, execute, flush
from tests import test_core_ui_1358_maintenance_preview_cancellation as harness


def test_details_are_additive_and_facts_are_never_stringified():
    class Private:
        def __str__(self):
            raise AssertionError('private execution facts rendered')
    result = MaintenanceResult(True, summary='Analyse terminée',
                               details=('Fichiers observés : 524',),
                               warnings=('Un ISBN ne prouve pas un droit.',),
                               facts={'secret': Private()})
    assert result_text(result) == ('Analyse terminée\n\nRésultats :\n• Fichiers observés : 524'
                                   '\n\nAvertissements et limites :\n• Un ISBN ne prouve pas un droit.')
    assert MaintenanceResult(True).details == ()


def test_malformed_and_oversized_plugin_output_is_bounded():
    result = NS(summary=object(), details=['x' * 10000] * 1000, warnings=object())
    text = result_text(result)
    assert text.startswith('Outil terminé.')
    assert len(text) < 140000
    assert 'object at' not in text


@pytest.mark.parametrize('ok', [True, False])
def test_completed_result_is_consultable_and_reopenable(ok, monkeypatch):
    destroyed = []
    monkeypatch.setattr(harness.Dialog, 'destroy', lambda self: destroyed.append(self), raising=False)
    m = mounted('library.reconciliation.audit')
    args = execute(m)
    args[2](MaintenanceResult(ok, summary='Bilan local', details=('Liens confirmés : 517',),
                              warnings=('Durée maximale atteinte.',), facts={'secret': '/private'}))
    flush(m)
    view = m.host._maintenance_result_views[0]
    assert view.button.visible
    # Reconciliation must show completion without a second unexplained click,
    # including ok=False when an offline SQLite/WAL snapshot is unavailable.
    assert view.dialog is not None
    dialog = view.dialog
    view.button.click()  # Reopening the already-visible dialog is idempotent.
    assert view.dialog is dialog
    label = dialog.extra_child.children[0]
    assert 'Liens confirmés : 517' in label.text
    assert 'Durée maximale atteinte.' in label.text
    assert '/private' not in label.text
    assert dialog.responses == {'close': 'Fermer', 'inspect': 'Examiner les cas…'}
    assert label.wrap and label.selectable
    dialog.handlers['response'](dialog, 'close')
    view.button.click()
    assert view.dialog is not dialog
    m.preview.click()
    # Starting a retry must not revoke the last valid reconciliation report.
    assert view.button.visible and 'Liens confirmés : 517' in view.text
    assert destroyed == []


@pytest.mark.parametrize('invalidated', ['identity', 'generation', 'detached', 'closed', 'refresh'])
def test_result_cannot_reopen_after_context_invalidation(invalidated, monkeypatch):
    monkeypatch.setattr(harness.Dialog, 'destroy', lambda self: None, raising=False)
    m = mounted('library.reconciliation.audit')
    args = execute(m)
    args[2](MaintenanceResult(True, details=('Détail local',)))
    view = m.host._maintenance_result_views[0]
    if invalidated == 'identity':
        m.host.service.current_identity = NS(identity_id='bob')
    elif invalidated == 'generation':
        m.host._identity_context_generation += 1
    elif invalidated == 'detached':
        m.preview.root = None
    elif invalidated == 'closed':
        m.host._closed = True
    else:
        m.refresh()
    view.button.click()
    assert view.dialog is None and view.text == ''


def test_cancelled_completion_never_publishes_result():
    m = mounted('library.reconciliation.audit')
    args = execute(m)
    m.cancel.click()
    args[2](MaintenanceResult(True, details=('Résultat périmé',)))
    view = m.host._maintenance_result_views[0]
    assert view.text == '' and view.button is None


def test_inspection_action_is_explicit_and_ignored_after_scope_change(monkeypatch):
    m = mounted('library.reconciliation.audit')
    args = execute(m)
    args[2](MaintenanceResult(True,summary='Bilan local'))
    view = m.host._maintenance_result_views[0]
    calls = []
    monkeypatch.setattr(view.inspector,'start',lambda:calls.append('fresh read'))
    view.button.click()
    dialog = view.dialog
    dialog.handlers['response'](dialog,'inspect')
    assert calls == ['fresh read']
    view.button.click()
    dialog = view.dialog
    m.host._identity_context_generation += 1
    dialog.handlers['response'](dialog,'inspect')
    assert calls == ['fresh read']


def test_incomplete_reconciliation_limits_are_visible_before_long_counters():
    # #2565 real audit: user initially saw 320/120/408 but had to scroll to
    # discover 120s inventory and 10s metadata-candidate limitations.
    result = MaintenanceResult(
        False,
        summary="Analyse incomplète. 320 fichier(s) observé(s), 120 lien(s) confirmé(s).",
        details=tuple(f"Compteur {i} : {i}" for i in range(60)),
        warnings=(
            "Durée maximale de l’inventaire atteinte.",
            "La recherche de correspondances bibliographiques reste incomplète.",
            "Un SHA ou un ISBN ne prouve pas un droit.",
        ),
        facts={"private_path": "/HOME_PRIVATE_2565",
               "private_sha": "PRIVATE_HASH_2565"},
    )
    rendered = result_text(result)
    assert rendered.startswith("Analyse incomplète.")
    assert rendered.index("Avertissements et limites") < rendered.index("Résultats")
    assert rendered.index("Durée maximale de l’inventaire atteinte") < rendered.index("Compteur 0")
    assert "Compteur 59 : 59" in rendered
    assert "PRIVATE" not in rendered
    assert len(rendered) < 12000


def test_other_maintenance_result_order_is_unchanged_and_malformed_status_is_safe():
    complete = MaintenanceResult(True, summary="Analyse terminée",
                                 details=("Fichiers observés : 1",),
                                 warnings=("Le droit reste indéterminé.",))
    text = result_text(complete)
    assert text.index("Résultats") < text.index("Avertissements et limites")
    unrelated = NS(summary="Outil interrompu", details=("Résultat",),
                   warnings=("Attention",), facts={"secret": object()})
    assert result_text(unrelated).index("Résultats") < result_text(unrelated).index("Avertissements")


def test_reconciliation_window_uses_real_adw_toolbar_not_message_dialog_in_gtk():
    """A physical GTK E2E asserts the resizable result before opening inspector."""
    from pathlib import Path
    probe = (Path(__file__).resolve().parents[1] /
             "e2e/library_inspection_acceptance.py").read_text(encoding="utf8")
    for expected in (
        "inspection_results_large_window_maximizable",
        "assert isinstance(results, Adw.Window)",
        "assert results.get_resizable() and results.get_decorated()",
        "isinstance(w, Adw.HeaderBar)",
        'w.get_label() == "Agrandir / Restaurer"',
    ):
        assert expected in probe


def test_other_maintenance_tools_retain_bounded_message_dialog_contract():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/maintenance_result_view.py").read_text(encoding="utf8")
    # The compatibility branch is explicit, not a silent universal migration
    # of confirmations, return actions or other Maintenance results.
    assert "self.inspector is not None" in source
    assert "getattr(self.Adw, 'ToolbarView', None)" in source
    assert 'dialog = self.Adw.MessageDialog.new(self.parent, self.title, "")' in source


def test_reconciliation_result_survives_ui_refresh_without_new_audit(monkeypatch):
    monkeypatch.setattr(harness.Dialog, 'destroy', lambda self: None, raising=False)
    m = mounted('library.reconciliation.audit')
    args = execute(m)
    args[2](MaintenanceResult(
        False, summary='Analyse incomplète. 290 fichiers.',
        details=('Liens confirmés : 102',), warnings=('Durée maximale atteinte.',),
    ))
    prior = m.host._maintenance_result_views[0]
    prior.dialog.handlers['response'](prior.dialog, 'close')
    count_before = len(m.jobs)
    m.refresh()
    assert len(m.jobs) == count_before
    new = m.host._maintenance_result_views[0]
    assert new is not prior and new.button.visible
    assert 'Liens confirmés : 102' in new.text
    assert 'Durée maximale atteinte.' in new.text
    new.button.click()
    assert 'Liens confirmés : 102' in new.dialog.extra_child.children[0].text


def test_reconciliation_retry_and_cancellation_retain_last_report(monkeypatch):
    monkeypatch.setattr(harness.Dialog, 'destroy', lambda self: None, raising=False)
    m = mounted('library.reconciliation.audit')
    args = execute(m)
    args[2](MaintenanceResult(False, summary='Analyse incomplète.',
                              details=('Ancien résultat : 321 fichiers',)))
    view = m.host._maintenance_result_views[0]
    view.dialog.handlers['response'](view.dialog, 'close')
    m.preview.click()
    assert view.button.visible and 'Ancien résultat : 321 fichiers' in view.text
    m.cancel.click()
    assert view.button.visible and 'Ancien résultat : 321 fichiers' in view.text
    # The in-memory session model is not discarded by a new preview.
    assert 'Ancien résultat : 321 fichiers' in m.host._maintenance_reconciliation_report['text']


@pytest.mark.parametrize('changed', ['identity', 'generation', 'epoch', 'storage'])
def test_reconciliation_cache_revoked_on_changed_scope(changed, monkeypatch):
    monkeypatch.setattr(harness.Dialog, 'destroy', lambda self: None, raising=False)
    m = mounted('library.reconciliation.audit')
    args = execute(m)
    args[2](MaintenanceResult(True, summary='PRIVATE_2732 result'))
    previous = m.host._maintenance_result_views[0]
    previous.dialog.handlers['response'](previous.dialog, 'close')
    if changed == 'identity':
        m.host.service.current_identity = NS(identity_id='bob')
    elif changed == 'generation':
        m.host._identity_context_generation += 1
    elif changed == 'epoch':
        m.host.service._account_context_epoch = 3
    else:
        m.host.service.storage = NS(path='/synthetic/other.sqlite')
    m.refresh()
    current = m.host._maintenance_result_views[0]
    assert current.text == ''
    assert current.button is None
    assert m.host._maintenance_reconciliation_report is None


def test_other_maintenance_results_still_clear_on_retry():
    m = mounted('covers.audit')
    args = execute(m)
    args[2](MaintenanceResult(True, summary='Ancien audit des couvertures'))
    view = m.host._maintenance_result_views[0]
    assert view.text
    m.preview.click()
    assert not view.text and not view.button.visible


def test_reconciliation_audit_and_inspection_have_distinct_action_rows():
    m = mounted('library.reconciliation.audit')
    rows = m.host._tools_box.children[0].children
    assert len(rows) == 2
    audit_row, inspection_row = rows
    assert audit_row is not inspection_row
    assert inspection_row.title == 'Inspection détaillée des cas'
    assert m.host._maintenance_result_views[0].inspector._activity_row is inspection_row
    assert m.feedback is m.host._maintenance_line_rows[0][0]
    # Other tools still use exactly one ActionRow.
    other = mounted('covers.audit')
    assert len(other.host._tools_box.children[0].children) == 1
