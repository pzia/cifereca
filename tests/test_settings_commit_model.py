"""Exercise the real headless settings commit coordinator, including delivery gaps."""
import os
import subprocess
import sys

from cifereca.gui.settings_model import SettingsCommitController


def controller(save=lambda payload: payload):
    queued, errors, deliveries = [], [], []
    model = SettingsCommitController(save, lambda *a, **kw: queued.append((a, kw)), errors.append)
    return model, queued, errors, deliveries


def test_staging_is_immutable_and_repeated_click_is_blocked_until_delivery():
    model, queued, errors, deliveries = controller()
    data = {'identities': [{'id': 'alice'}]}
    assert model.submit('save', data, deliveries.append)
    data['identities'][0]['id'] = 'bob'
    args, options = queued[0]
    assert options['kind'] == 'settings'
    result = args[1]()
    assert result['identities'][0]['id'] == 'alice'
    assert model.pending
    assert not model.submit('second', data, deliveries.append)
    assert len(queued) == 1 and deliveries == []
    args[2](result)
    assert deliveries == [result] and not model.pending and errors == []


def test_error_releases_pending_save_without_success_or_reconnect():
    def save(_payload):
        raise RuntimeError('migration rollback')
    model, queued, errors, deliveries = controller(save)
    assert model.submit('save', {}, deliveries.append)
    args, _ = queued[0]
    try:
        args[1]()
    except RuntimeError as exc:
        args[3](exc)
    assert len(errors) == 1 and str(errors[0]) == 'migration rollback'
    assert not model.pending and deliveries == []
    assert model.submit('retry', {}, deliveries.append)


def test_stale_duplicate_completion_cannot_finish_a_new_save():
    model, queued, errors, deliveries = controller()
    assert model.submit('first', {}, deliveries.append)
    first, _ = queued[0]
    first[2]('one')
    assert model.submit('second', {}, deliveries.append)
    first[2]('stale'); first[3](RuntimeError('stale'))
    assert model.pending and deliveries == ['one'] and errors == []
    second, _ = queued[1]
    second[2]('two')
    assert not model.pending and deliveries == ['one', 'two']


def test_disposal_suppresses_ui_delivery_and_rejects_new_submissions():
    model, queued, errors, deliveries = controller()
    assert model.submit('save', {}, deliveries.append)
    args, _ = queued[0]
    model.dispose()
    args[2]('committed'); args[3](RuntimeError('late'))
    assert deliveries == [] and errors == []
    assert not model.submit('new', {}, deliveries.append)


def test_scheduler_rejection_is_reported_and_does_not_leave_a_pending_save():
    errors = []
    def reject(*_args, **_kwargs):
        raise RuntimeError('scheduler closed')
    model = SettingsCommitController(lambda payload: payload, reject, errors.append)
    assert not model.submit('save', {}, lambda value: None)
    assert not model.pending and len(errors) == 1


def test_model_import_requires_neither_gtk_nor_main_window():
    code = '''
import importlib.abc, sys
class NoGtk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'gi' or fullname.startswith('gi.'):
            raise AssertionError('settings model must be headless')
sys.meta_path.insert(0, NoGtk())
from cifereca.gui.settings_model import SettingsCommitController, SettingsRevisionState
from cifereca.gui.settings_draft import build_settings_draft
assert 'cifereca.gui.main_window' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', code], env=os.environ.copy(),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
