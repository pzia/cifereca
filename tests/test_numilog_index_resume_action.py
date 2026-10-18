"""Passive local receipt selection and exact offline worker resumption."""
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from tests.test_numilog_owned_library_download import case
from cifereca.core.numilog_materialization_checkpoint import NumilogIndexResumeTarget
from cifereca.providers import ProviderConfigurationError


def test_passive_target_never_hashes_or_uses_provider_and_resume_is_offline(case, monkeypatch):
    import cifereca.core.numilog_materialization_checkpoint as checkpoint
    acquired = case.service.acquire_direct_item(case.item)
    case.service.accounts = []
    case.service._sessions.clear()
    original = checkpoint._read_hash_within
    def forbidden(*_a, **_k):raise AssertionError('file read during render')
    monkeypatch.setattr(checkpoint, '_read_hash_within', forbidden)
    target = case.service.owned_library_download_target(case.entry)
    assert type(target) is NumilogIndexResumeTarget
    assert target.item_id not in repr(target) and str(acquired.path) not in repr(target)
    assert len(case.calls) == 1 and case.inventory == []
    monkeypatch.setattr(checkpoint, '_read_hash_within', original)
    result = case.service.obtain_owned_library_item(target)
    assert result.path == acquired.path and result.indexed['status'] == 'indexed'
    assert len(case.calls) == 1 and case.inventory == []
    assert case.service.owned_library_download_target(case.entry) is None


@pytest.mark.parametrize('mutation', ['identity', 'directory', 'bytes', 'scope', 'ledger', 'other_purchase'])
def test_stale_resume_target_refuses_without_index_or_download(case, monkeypatch, mutation):
    acquired = case.service.acquire_direct_item(case.item)
    target = case.service.owned_library_download_target(case.entry)
    assert type(target) is NumilogIndexResumeTarget
    if mutation == 'identity':case.service.current_identity = NS(identity_id='alex')
    if mutation == 'directory':case.service.config.set('library.directory', '/another-library')
    if mutation == 'bytes':acquired.path.write_bytes(b'changed')
    if mutation == 'scope':target = replace(target, scope=('forged',))
    if mutation == 'other_purchase':target = replace(target, item_id='another-purchase')
    if mutation == 'ledger':
        row = case.service.storage.acquisition_get(target.acquisition_id)
        case.service.storage.acquisition_put({**row, 'acquisition_id':'competing'})
    indexed=[]
    monkeypatch.setattr(case.service, 'catalog_index_path', lambda *_a, **_k:indexed.append(True))
    with pytest.raises(ProviderConfigurationError):case.service.obtain_owned_library_item(target)
    assert indexed == [] and len(case.calls) == 1


def test_receipt_changed_while_entering_index_lock_rejects_stale_button(case, monkeypatch):
    import cifereca.core.numilog_materialization_checkpoint as checkpoint
    acquired = case.service.acquire_direct_item(case.item)
    target = case.service.owned_library_download_target(case.entry)
    original = checkpoint.InterProcessLock
    class Changed(original):
        def acquire(self):
            super().acquire()
            row=case.service.storage.acquisition_get(target.acquisition_id)
            row['metadata']['phase']='indexed'
            case.service.storage.acquisition_put(row)
    monkeypatch.setattr(checkpoint, 'InterProcessLock', Changed)
    indexed=[]
    monkeypatch.setattr(case.service, 'catalog_index_path', lambda *_a, **_k:indexed.append(True))
    with pytest.raises(ProviderConfigurationError):case.service.obtain_owned_library_item(target)
    assert indexed == [] and len(case.calls) == 1


def test_ambiguous_possessions_or_missing_receipt_never_proposes_resume(case):
    assert not isinstance(case.service.owned_library_download_target(case.entry), NumilogIndexResumeTarget)
    case.service.acquire_direct_item(case.item)
    entry={**case.entry, 'possessions':[*case.entry['possessions'],
        {**case.entry['possessions'][0], 'provider_id':'kobo'}]}
    assert case.service.owned_library_download_target(entry) is None
