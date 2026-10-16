"""CoreUI #1885 acceptance on a real MainWindow/TaskRunner, with offline jobs.

Imported by the existing 1366/1600 baseline; no new runner or fake GTK widgets.
No actual provider, acquisition, signed URL or purchased content is accessed.
"""
from time import monotonic


def exercise(window, *, Gtk, Adw, walk, device, report, capture, later, done):
    from cifereca.gui.activity_feedback import ActivityFeedback
    from cifereca.gui.task_runner import TaskPriority

    original_feedback = window.activity_feedback

    def wait_for(predicate, callback):
        deadline = monotonic() + 5
        def poll():
            if predicate():
                callback()
            elif monotonic() >= deadline:
                raise AssertionError('P0 #1885 activity state timeout')
            else:
                later(60, poll)
        later(60, poll)

    def dialogs():
        return [node for node in Gtk.Window.list_toplevels()
                if isinstance(node, Adw.MessageDialog) and node.get_visible()]

    def failed_job():
        raise RuntimeError('https://example.test/URLLink.acsm?auth=E2E_PRIVATE_SENTINEL')

    def begin():
        window.activity_feedback = ActivityFeedback()
        window.runner.submit(
            'Obtention depuis numilog…', failed_job, None,
            lambda _error: window.show_error('Téléchargement Numilog indisponible', 'Obtention Numilog'),
            priority=TaskPriority.USER_ACTION, kind='obtain',
        )
        wait_for(lambda: not window._active_tasks and bool(dialogs()), close_dialog)

    def close_dialog():
        dialog = next(node for node in dialogs() if node.get_heading() == 'Obtention Numilog')
        assert window.activity_feedback.latest_unacknowledged_error() is not None
        assert 'Télécharger un achat Numilog' in window.busy_label.get_label()
        assert 'E2E_PRIVATE_SENTINEL' not in window.busy_label.get_label()
        close = next(node for node in walk(dialog) if isinstance(node, Gtk.Button)
                     and str(node.get_label()).replace('_', '') == 'Fermer')
        report['pointer_actions'].append(device.click_widget(close))
        wait_for(lambda: not dialogs(), local_search)

    def local_search():
        # The real TaskRunner, history, banner and GTK main-loop delivery are
        # exercised; these deterministic jobs do not claim provider HTTP proof.
        window.runner.submit('Recherche dans le connu…', lambda: 18, lambda _result: None,
                             priority=TaskPriority.USER_ACTION, kind='search-known',
                             success_toast='Recherche dans le connu : 18 résultats')
        wait_for(lambda: not window._active_tasks and window.busy_label.get_label().startswith(
            'Terminé · Recherche dans le connu : 18 résultats'), online_search)

    def online_search():
        assert 'Erreur antérieure · Télécharger un achat Numilog' in window.busy_label.get_label()
        assert window.activity_feedback.latest_unacknowledged_error() is not None
        window.runner.submit('Recherche en ligne…', lambda: 20, lambda _result: None,
                             priority=TaskPriority.USER_ACTION, kind='search-online',
                             success_toast='Recherche en ligne : 20 résultats')
        wait_for(lambda: not window._active_tasks and window.busy_label.get_label().startswith(
            'Terminé · Recherche en ligne : 20 résultats'), show_combined)

    def show_combined():
        text = window.busy_label.get_label()
        assert 'Erreur antérieure · Télécharger un achat Numilog' in text
        assert 'E2E_PRIVATE_SENTINEL' not in text and 'https://' not in text
        assert window.activity_status_icon.has_css_class('activity-success')
        assert window.activity_dismiss.get_visible() and window.activity_dismiss.get_label() == 'Acquitter'
        assert len(window.activity_feedback.rows()) == 3
        window._hide_activity_feedback()
        assert window.busy_label.get_label() == text
        # Let the real revealer finish its slide before the physical click.
        later(320, acknowledge)

    def acknowledge():
        capture('03a-numilog-prior-error-after-search-success')
        report['pointer_actions'].append(device.click_widget(window.activity_dismiss))
        wait_for(lambda: window.activity_feedback.latest_unacknowledged_error() is None, new_failure)

    def new_failure():
        assert len(window.activity_feedback.rows()) == 3
        window.runner.submit('Recherche en ligne…', failed_job, None, lambda _error: None,
                             priority=TaskPriority.USER_ACTION, kind='search-online')
        wait_for(lambda: not window._active_tasks and window.activity_feedback.latest_unacknowledged_error()
                 is not None, finish)

    def finish():
        assert window.busy_label.get_label().startswith('Erreur · Recherche en ligne')
        assert 'Erreur antérieure' not in window.busy_label.get_label()
        assert len(window.activity_feedback.rows()) == 4
        report['checks']['P0_1885_real_gtk_prior_numilog_error_distinct_from_search_success'] = True
        report['checks']['P0_1885_xtest_close_ack_preserves_history_and_new_error'] = True
        report['checks']['P0_1885_no_provider_or_acquisition_call_in_offline_jobs'] = True
        window._cancel_activity_hide()
        window.activity_feedback = original_feedback
        window._refresh_activity_history()
        window._hide_activity_feedback()
        done()

    wait_for(lambda: not window._active_tasks, begin)
