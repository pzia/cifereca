"""Targeted rights callbacks cannot be ignored, retargeted or leave the lock."""
from dataclasses import replace
import pytest
from cifereca.providers import ProviderConfigurationError
from cifereca.plugin_api import Cancelled
from tests.test_core_p0_numilog_manual_acquisition_lock import _rig


def test_targeted_admission_runs_inside_lock_before_provider_and_before_publication(tmp_path, monkeypatch):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    events, locked = [], []
    class Lock:
        def __init__(self, *_a):pass
        def __enter__(self):locked.append(True)
        def __exit__(self, *_a):locked.clear()
    monkeypatch.setattr('cifereca.services.InterProcessLock', Lock)
    def admit():
        assert locked
        events.append(tuple(calls))
        return replace(item)
    try:
        result = service.acquire_direct_item(item, _admission=admit)
        assert events == [(), (item.record_id,), (item.record_id, 'post_download')]
        assert result.item_id == item.record_id and result.account_id == account.unique_id
        assert result.metadata['identity_id'] == account.identity_id
        assert not locked and result.local_path.is_file()
    finally:service.close()


@pytest.mark.parametrize('field,value', [
    ('source_provider','numilog-catalog'), ('source_credential','other'),
    ('record_id','another-purchase'), ('revision_id','another-revision'),
    ('source_collection','public_catalog'), ('format','pdf'),
    ('downloadable',False), ('id','another-row'),
])
def test_targeted_admission_cannot_change_purchase_or_authority(tmp_path, monkeypatch, field, value):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    try:
        with pytest.raises(ProviderConfigurationError, match='autorisation ciblée'):
            service.acquire_direct_item(item, _admission=lambda: replace(item, **{field:value}))
        assert calls == []
    finally:service.close()


@pytest.mark.parametrize('refusal_at', [1, 2, 3])
def test_revoked_rights_before_or_after_io_or_hook_never_publish(tmp_path, monkeypatch, refusal_at):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    count = []
    publications = []
    original = service.acquisition.acquisition_from_direct
    def publish(*a, **kw):publications.append(True);return original(*a, **kw)
    monkeypatch.setattr(service.acquisition, 'acquisition_from_direct', publish)
    def admit():
        count.append(True)
        if len(count) == refusal_at:raise RuntimeError('?auth=PRIVATE_SIGNED_LINK')
        return item
    try:
        with pytest.raises(ProviderConfigurationError, match='autorisation ciblée') as exc:
            service.acquire_direct_item(item, _admission=admit)
        assert 'PRIVATE_SIGNED_LINK' not in str(exc.value) and publications == []
        assert len(count) == refusal_at
        assert calls == ( [] if refusal_at == 1 else
                         [item.record_id] if refusal_at == 2 else
                         [item.record_id, 'post_download'])
    finally:service.close()


def test_callback_cancellation_is_preserved_and_never_downloads(tmp_path, monkeypatch):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    def admit():raise Cancelled()
    try:
        with pytest.raises(Cancelled):service.acquire_direct_item(item, _admission=admit)
        assert calls == []
    finally:service.close()



def test_source_reconnect_inside_admission_never_reaches_provider(tmp_path, monkeypatch):
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    def admit():
        service._sessions.clear()
        return item
    try:
        with pytest.raises(ProviderConfigurationError, match='autorisation ciblée'):
            service.acquire_direct_item(item, _admission=admit)
        assert calls == []
    finally:service.close()


def test_busy_lock_never_executes_callback_or_provider(tmp_path, monkeypatch):
    from cifereca.locking import LockBusyError
    service, account, session, item, calls, records = _rig(tmp_path, monkeypatch)
    callback_calls = []
    class Busy:
        def __init__(self, *_a):pass
        def __enter__(self):raise LockBusyError('busy')
        def __exit__(self, *_a):pass
    monkeypatch.setattr('cifereca.services.InterProcessLock', Busy)
    try:
        with pytest.raises(ProviderConfigurationError, match='déjà en cours'):
            service.acquire_direct_item(item, _admission=lambda: callback_calls.append(True) or item)
        assert callback_calls == [] and calls == []
    finally:service.close()
