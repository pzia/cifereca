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
    assert view.dialog is None  # no modal interrupts completion or the next task
    view.button.click()
    dialog = view.dialog
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
    assert not view.button.visible and view.text == ''
    assert destroyed


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
