"""P0 #1885: a past Numilog acquisition error cannot replace search success."""
import pytest

from cifereca.gui.activity_feedback import ActivityFeedback
from cifereca.gui.activity_feedback_view_controller import show_pending_activity_error, dismiss_activity_feedback
from cifereca.gui.activity_result_controller import activity_result
from cifereca.gui.task_activity_adapter import task_started, task_finished
from tests.test_core_ui_1132_activity_feedback_view_controller import context


def window_with_clock():
    now = [100.0]
    window = context()
    window.activity_feedback = ActivityFeedback(clock=lambda: now[0])
    window.busy_spinner.start = lambda: None
    window._active_tasks = {}
    window._finished_task_ids = set()
    window._last_finished_task_id = None
    window._functional_error_message = lambda value: value
    window._schedule_activity_reduce = lambda: None
    window._cancel_activity_hide = lambda: None
    return window, now


def complete(window, task_id, label, kind, success=True, error=None):
    task_started(window, task_id, 1, label, 1, kind)
    task_finished(window, task_id, 0, label, success, error)


def test_failed_numilog_then_local_and_online_success_preserve_both_results_and_acknowledgement():
    window, now = window_with_clock()
    complete(window, 1, 'Obtention depuis numilog…', 'obtain', False, 'Lien de livraison absent')
    assert 'Télécharger un achat Numilog' in window.busy_label.label
    for task_id, label, kind in [(2, 'Recherche dans le connu…', 'search-known'),
                                 (3, 'Recherche en ligne…', 'search-online')]:
        now[0] += 120
        complete(window, task_id, label, kind)
        text = window.busy_label.label
        assert text.startswith('Terminé · ' + label + '\nErreur antérieure · Télécharger un achat Numilog')
        assert 'il y a' in text and 'Lien de livraison absent' in text
        assert 'activity-success' in window.activity_status_icon.classes
        assert window.activity_feedback.latest_unacknowledged_error().task_id == 1
        # The hide timer must keep the combined status instead of reverting
        # to an error-only banner or silently acknowledging history.
        window._hide_activity_feedback()
        assert window.busy_label.label == text
    assert len(window.activity_feedback.rows()) == 3
    dismiss_activity_feedback(window)
    assert window.activity_feedback.latest_unacknowledged_error() is None
    assert len(window.activity_feedback.rows()) == 3
    complete(window, 4, 'Recherche en ligne…', 'search-online', False, 'Catalogues indisponibles')
    assert window.busy_label.label.startswith('Erreur · Recherche en ligne')
    assert 'Erreur antérieure' not in window.busy_label.label


def test_result_callback_keeps_own_message_and_old_error_distinct():
    window, now = window_with_clock()
    complete(window, 1, 'Obtention depuis numilog…', 'obtain', False, 'Refus')
    complete(window, 2, 'Recherche dans le connu…', 'search-known')
    activity_result(window, '18 résultats connus', task_id=2)
    assert window.busy_label.label.startswith('Terminé · 18 résultats connus\nErreur antérieure')
    assert len(window.activity_feedback.rows()) == 2


def test_concurrent_completion_order_not_task_start_order_decides_which_failure_is_latest():
    window, now = window_with_clock()
    task_started(window, 1, 1, 'Obtention depuis numilog…', 1, 'obtain')
    task_started(window, 2, 2, 'Recherche en ligne…', 1, 'search-online')
    task_finished(window, 2, 1, 'Recherche en ligne…', False, 'Recherche impossible')
    now[0] += 1
    task_finished(window, 1, 0, 'Obtention depuis numilog…', False, 'Obtention impossible')
    assert window.activity_feedback.latest_unacknowledged_error().task_id == 1
    assert window.activity_feedback.rows()[0].task_id == 1
    assert 'Télécharger un achat Numilog' in window.busy_label.label
    # Duplicate callbacks cannot create another history row or re-acknowledge.
    window.activity_feedback.acknowledge_errors()
    task_finished(window, 1, 0, 'Obtention depuis numilog…', False, 'Duplicate')
    assert window.activity_feedback.latest_unacknowledged_error() is None


@pytest.mark.parametrize('error', [
    'https://example.test/URLLink.acsm?auth=PRIVATE_SENTINEL',
    'reader-PRIVATE_SENTINEL@example.test', 'auth=PRIVATE_SENTINEL',
    '"token": "PRIVATE_SENTINEL"', '/home/PRIVATE_SENTINEL/book.epub',
])
def test_untrusted_failure_is_not_retained_in_activity_or_banner(error):
    window, now = window_with_clock()
    complete(window, 1, 'Obtention depuis numilog…', 'obtain', False, error)
    assert 'PRIVATE_SENTINEL' not in repr(window.activity_feedback.rows())
    assert 'PRIVATE_SENTINEL' not in window.busy_label.label
    assert 'diagnostics expurgés' in window.busy_label.label


def test_equal_clock_readings_and_new_background_starts_cannot_relabel_a_search_success():
    window, now = window_with_clock()
    complete(window, 1, 'Obtention depuis numilog…', 'obtain', False, 'Refus')
    complete(window, 2, 'Recherche dans le connu…', 'search-known')
    window.activity_feedback.started(3, 'Lecture arrière-plan', 'library')
    assert show_pending_activity_error(window)
    assert window.busy_label.label.startswith('Terminé · Recherche dans le connu…\nErreur antérieure')


def test_inventory_failure_has_an_explicit_scope_distinct_from_search_and_download():
    feedback = ActivityFeedback()
    entry = feedback.finished(1, 'Actualiser mes achats Numilog…', 'source-sync', success=False, error='Bibliothèque inaccessible')
    assert entry.operation_label == 'Actualiser mes achats Numilog'
    assert entry.display_text.startswith('Erreur · Actualiser mes achats Numilog')
