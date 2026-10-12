"""Opening coordination rejects redundant work through the facade lock."""
import pytest

from cifereca import services
from cifereca.services import LockBusyError
from cifereca.providers import ProviderConfigurationError
from tests.test_sync_state_contract import _service


def test_nested_opening_request_is_rejected_without_duplicate_work(tmp_path, monkeypatch):
    service = _service(tmp_path)
    entered = False
    paths = []
    calls = []

    class ControlledLock:
        def __init__(self, path):
            paths.append(path)

        def __enter__(self):
            nonlocal entered
            if entered:
                raise LockBusyError('busy')
            entered = True

        def __exit__(self, *_args):
            nonlocal entered
            entered = False

    def run(*, progress=None, deep=False):
        calls.append(deep)
        with pytest.raises(ProviderConfigurationError, match='déjà en cours'):
            service.opening_sync(deep=True)
        return {'identities': []}

    monkeypatch.setattr(services, 'InterProcessLock', ControlledLock)
    monkeypatch.setattr(services, 'acquisition_lock_file', lambda: tmp_path / 'shared.lock')
    monkeypatch.setattr(service, '_opening_sync_locked', run)
    try:
        assert service.opening_sync() == {'identities': []}
        assert calls == [False]
        assert paths == [tmp_path / 'shared.lock'] * 2
        assert not entered
        assert service.opening_sync(deep=True) == {'identities': []}
        assert calls == [False, True]
    finally:
        service.close()


def test_progress_exception_does_not_change_fast_sync_result(tmp_path, monkeypatch):
    service = _service(tmp_path)
    events = []
    service.accounts = []

    def broken_progress(event):
        events.append(event)
        raise RuntimeError('UI observer unavailable')

    monkeypatch.setattr(service, 'kobo_backup_enabled', lambda: False)
    monkeypatch.setattr(service, 'mirror_personal_libraries', lambda: {})
    monkeypatch.setattr(service, '_reconcile_identity_adept_inventory', lambda _id: {})
    monkeypatch.setattr(service, 'catalog_scan', lambda **_kw: pytest.fail('fast sync must not scan'))
    try:
        result = service.opening_sync(progress=broken_progress)
        assert result['sync_mode'] == 'fast'
        assert result['catalog']['filesystem_scan'] == 'skipped'
        assert [r['identity_id'] for r in result['identities']] == ['default', 'alex']
        assert all(r['status'] == 'success' for r in result['identities'])
        assert len([e for e in events if e['event'] == 'identity_begin']) == 2
        assert service.storage.latest_sync_run('default')['status'] == 'success'
        assert service.storage.latest_sync_run('alex')['status'] == 'success'
    finally:
        service.close()
