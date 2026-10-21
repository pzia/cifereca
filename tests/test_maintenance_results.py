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
    assert len(rows) == 3
    audit_row, last_report_row, inspection_row = rows
    assert audit_row is not last_report_row
    assert last_report_row is not inspection_row
    assert last_report_row.title == 'Dernière analyse : aucune'
    assert inspection_row.title == 'Inspection détaillée des cas'
    assert m.host._maintenance_result_views[0].inspector._activity_row is inspection_row
    assert m.feedback is m.host._maintenance_line_rows[0][0]
    # Other tools still use exactly one ActionRow.
    other = mounted('covers.audit')
    assert len(other.host._tools_box.children[0].children) == 1


@pytest.mark.parametrize(('ok', 'summary', 'expected'), [
    (False, 'Analyse incomplète. 290 fichiers observés.', 'incomplète'),
    (True, 'Analyse terminée. 290 fichiers observés.', 'terminée'),
    (False, 'Inspection impossible : snapshot indisponible.', 'indisponible'),
])
def test_last_report_state_is_distinct_from_activity_and_persists(
    ok, summary, expected, monkeypatch,
):
    monkeypatch.setattr(harness.Dialog, 'destroy', lambda self: None, raising=False)
    m = mounted('library.reconciliation.audit')
    audit, last, inspection = m.host._tools_box.children[0].children
    args = execute(m)
    args[2](MaintenanceResult(ok, summary=summary))
    view = m.host._maintenance_result_views[0]
    assert view.state == expected
    assert last.title == f'Dernière analyse : {expected}'
    assert last.subtitle.startswith('Rapport conservé')
    assert inspection.title == 'Inspection détaillée des cas'
    assert 'Dernière analyse' not in getattr(audit, 'title', '')
    assert view.button in last.children
    assert view.button not in audit.children
    assert m.host._maintenance_reconciliation_report['state'] == expected
    view.dialog.handlers['response'](view.dialog, 'close')
    m.refresh()
    audit, last, inspection = m.host._tools_box.children[0].children
    assert last.title == f'Dernière analyse : {expected}'
    assert m.host._maintenance_result_views[0].button.visible
    assert inspection.title == 'Inspection détaillée des cas'
    # A new attempt only manipulates the audit row, not the last report.
    m.preview.click()
    assert last.title == f'Dernière analyse : {expected}'


def test_last_report_scope_change_restores_neither_text_nor_label(monkeypatch):
    monkeypatch.setattr(harness.Dialog, 'destroy', lambda self: None, raising=False)
    m = mounted('library.reconciliation.audit')
    args = execute(m)
    args[2](MaintenanceResult(False, summary='Analyse incomplète. PRIVATE_2732'))
    view = m.host._maintenance_result_views[0]
    view.dialog.handlers['response'](view.dialog, 'close')
    m.host.service.current_identity = NS(identity_id='other')
    m.refresh()
    _audit, last, _inspection = m.host._tools_box.children[0].children
    assert last.title == 'Dernière analyse : aucune'
    assert m.host._maintenance_result_views[0].text == ''
    assert m.host._maintenance_reconciliation_report is None
    assert 'PRIVATE_2732' not in last.title


def test_running_reconciliation_survives_refresh_and_publishes_partial_result(monkeypatch):
    monkeypatch.setattr(harness.Dialog, 'destroy', lambda self: None, raising=False)
    m = mounted('library.reconciliation.audit')
    args = execute(m)
    original = m.host._maintenance_line_rows[0][1]['control']
    assert not original.cancelled()
    m.refresh()
    assert not original.cancelled()  # UI refresh must not cancel a safe read.
    view = m.host._maintenance_result_views[0]
    assert not view.text
    assert m.host._maintenance_reconciliation_run['view'] is view
    assert len(m.jobs) == 2  # plan + run; refresh doesn't enqueue another scan.
    original.report(current=291, total=None, message='Inventaire local')
    flush(m)
    status = m.host._maintenance_line_rows[0][0].state
    assert status.label.endswith('291 fichier(s)')
    args[2](MaintenanceResult(
        False, summary='Analyse incomplète. 291 fichiers observés.',
        details=('Droits sans fichier vérifié : 408',),
        warnings=('Durée maximale de l’inventaire atteinte.',),
    ))
    assert m.host._maintenance_reconciliation_run is None
    assert view.state == 'incomplète'
    assert view.dialog is None  # no unexpected popup after refresh
    assert view.button.visible
    assert '408' in view.text
    assert m.host._maintenance_reconciliation_report['text'] == view.text
    m.refresh()
    restored = m.host._maintenance_result_views[0]
    assert restored.button.visible
    assert '408' in restored.text
    assert len(m.jobs) == 2


def test_refreshed_cancel_stops_old_worker_and_cannot_overwrite_report(monkeypatch):
    monkeypatch.setattr(harness.Dialog, 'destroy', lambda self: None, raising=False)
    m = mounted('library.reconciliation.audit')
    initial = execute(m)
    initial[2](MaintenanceResult(False, summary='Analyse incomplète. Ancien rapport'))
    original_report = m.host._maintenance_reconciliation_report.copy()
    view = m.host._maintenance_result_views[0]
    view.dialog.handlers['response'](view.dialog, 'close')
    pending = execute(m)
    control = m.host._maintenance_line_rows[0][1]['control']
    m.refresh()
    new_cancel = m.host._tools_box.children[0].children[0].children[1]
    assert new_cancel.visible
    new_cancel.click()
    assert control.cancelled()
    pending[2](MaintenanceResult(True, summary='Nouveau rapport interdit'))
    assert m.host._maintenance_reconciliation_report == original_report
    assert 'Ancien rapport' in m.host._maintenance_result_views[0].text
    assert 'Nouveau rapport' not in m.host._maintenance_result_views[0].text


@pytest.mark.parametrize('changed', ['identity', 'generation', 'epoch', 'storage'])
def test_scope_change_cancels_retained_reconciliation_without_cross_identity_leak(
    changed, monkeypatch,
):
    monkeypatch.setattr(harness.Dialog, 'destroy', lambda self: None, raising=False)
    m = mounted('library.reconciliation.audit')
    pending = execute(m)
    control = m.host._maintenance_line_rows[0][1]['control']
    if changed == 'identity':
        m.host.service.current_identity = NS(identity_id='bob')
    elif changed == 'generation':
        m.host._identity_context_generation += 1
    elif changed == 'epoch':
        m.host.service._account_context_epoch = 42
    else:
        m.host.service.storage = NS(path='/synthetic/new.sqlite')
    m.refresh()
    assert control.cancelled()
    pending[2](MaintenanceResult(True, summary='PRIVATE_2732_FOREIGN'))
    assert m.host._maintenance_reconciliation_run is None
    assert m.host._maintenance_reconciliation_report is None
    view = m.host._maintenance_result_views[0]
    assert view.text == '' and view.button is None
    assert all('PRIVATE_2732_FOREIGN' not in str(args)
               for args, _kwargs in m.activity)


def test_refresh_while_running_blocks_another_reconciliation_attempt():
    m = mounted('library.reconciliation.audit')
    _pending = execute(m)
    m.refresh()
    button = m.host._tools_box.children[0].children[0].children[0]
    assert not button.sensitive
    count = len(m.jobs)
    button.click()  # even adversarial direct signal can't bypass the active guard
    assert len(m.jobs) == count


def test_other_maintenance_refresh_still_cancels_pending_work():
    m = mounted('covers.audit')
    _pending = execute(m)
    control = m.host._maintenance_line_rows[0][1]['control']
    m.refresh()
    assert control.cancelled()


def test_in_place_library_root_change_revokes_unfinished_reconciliation(tmp_path):
    m = mounted('library.reconciliation.audit')

    class MutableConfiguration:
        def __init__(self):
            self.data = {'library.directory': str(tmp_path / 'one')}
        def get(self, key, default=None):
            return self.data.get(key, default)
        def identities(self):
            return [{'id': 'alice', 'directory_name': 'alice'}]

    config = MutableConfiguration()
    m.host.service.config = config
    pending = execute(m)
    control = m.host._maintenance_line_rows[0][1]['control']
    initial_data_id = id(config.data)
    config.data['library.directory'] = str(tmp_path / 'two')
    assert id(config.data) == initial_data_id
    m.refresh()
    assert control.cancelled()
    pending[2](MaintenanceResult(True, summary='PRIVATE_STALE_ROOT'))
    assert m.host._maintenance_reconciliation_report is None
    assert m.host._maintenance_result_views[0].text == ''
