"""Numilog delivery -> index must have durable exclusive provenance."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from cifereca.core.owned_library_download import OwnedDownloadRejected
from tests.test_numilog_owned_library_download import case


def test_second_click_during_indexing_never_repeats_provider(case, monkeypatch):
    target = case.service.owned_library_download_target(case.entry)
    in_index, release = threading.Event(), threading.Event()
    original = case.service.catalog_index_path
    index_calls = []
    def index(*args, **kwargs):
        index_calls.append(True)
        if len(index_calls) == 1:
            in_index.set()
            assert release.wait(4)
        return original(*args, **kwargs)
    monkeypatch.setattr(case.service, 'catalog_index_path', index)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(case.service.obtain_owned_library_item, target)
        try:
            assert in_index.wait(2)
            with pytest.raises(OwnedDownloadRejected):
                case.service.obtain_owned_library_item(target)
            assert case.calls == [case.item.record_id]
        finally:
            release.set()
            first.result(timeout=4)


def test_checkpoint_is_complete_only_after_matching_artifact_and_placement(case):
    acquired = case.service.acquire_direct_item(case.item)
    checkpoint_id = acquired.metadata['numilog_checkpoint_id']
    row = case.service.storage.acquisition_get(checkpoint_id)
    assert row['status'] == 'attention_required'
    assert row['metadata']['phase'] == 'index_pending' and row['artifact_id'] is None
    assert row['metadata']['sha256'] and row['metadata']['size'] > 0
    assert 'PRIVATE' not in repr(row) and 'https://' not in repr(row)
    result = case.service.resume_numilog_materialization(checkpoint_id)
    row = case.service.storage.acquisition_get(checkpoint_id)
    assert row['status'] == 'complete' and row['artifact_id'] == result.artifact_id
    assert row['metadata']['phase'] == 'indexed'
    assert case.calls == [case.item.record_id]


def test_failed_index_leaves_receipt_for_offline_resume_without_connected_accounts(case, monkeypatch):
    from cifereca.providers import ProviderConfigurationError
    original = case.service.catalog_index_path
    monkeypatch.setattr(case.service, 'catalog_index_path', lambda *_a, **_k: {'status':'invalid','reason':'PRIVATE'})
    with pytest.raises(ProviderConfigurationError) as exc:case.service.obtain_search_item(case.item)
    assert 'PRIVATE' not in str(exc.value)
    row = case.service.storage.acquisitions(provider_id='numilog')[0]
    assert row['status'] == 'attention_required' and row['metadata']['phase'] == 'index_pending'
    monkeypatch.setattr(case.service, 'catalog_index_path', original)
    case.service.accounts = []
    case.service._sessions.clear()
    result = case.service.resume_numilog_materialization(row['acquisition_id'])
    assert result.path.is_file() and result.indexed['status'] == 'indexed'
    assert case.calls == [case.item.record_id] and case.inventory == []
    again = case.service.resume_numilog_materialization(row['acquisition_id'])
    assert again.artifact_id == result.artifact_id and not again.indexed['artifact_new']
    assert len(case.calls) == 1


@pytest.mark.parametrize('mutation', ['bytes','missing','symlink','directory','identity','rebound','ambiguous_ledger'])
def test_changed_or_ambiguous_receipt_never_indexes_or_downloads(case, monkeypatch, tmp_path, mutation):
    from cifereca.providers import ProviderConfigurationError
    from types import SimpleNamespace as NS
    acquired = case.service.acquire_direct_item(case.item)
    checkpoint_id = acquired.metadata['numilog_checkpoint_id']
    if mutation == 'bytes':acquired.path.write_bytes(b'changed bytes')
    if mutation == 'missing':acquired.path.unlink()
    if mutation == 'symlink':
        other=tmp_path/'outside.epub';other.write_bytes(acquired.path.read_bytes())
        acquired.path.unlink();acquired.path.symlink_to(other)
    if mutation == 'directory':case.service.config.set('library.directory',str(tmp_path/'other-library'))
    if mutation == 'identity':case.service.current_identity=NS(identity_id='alex')
    if mutation == 'rebound':case.service.storage.provider_connection_put({
        'identity_id':'alex','provider_id':'numilog','credential_id':case.account.credential_id})
    if mutation == 'ambiguous_ledger':
        row=case.service.storage.acquisition_get(checkpoint_id)
        case.service.storage.acquisition_put({**row,'acquisition_id':'competing-acquisition'})
    indexed=[]
    monkeypatch.setattr(case.service,'catalog_index_path',lambda *_a,**_k:indexed.append(True))
    with pytest.raises(ProviderConfigurationError):case.service.resume_numilog_materialization(checkpoint_id)
    assert indexed == [] and case.calls == [case.item.record_id]
    assert case.service.storage.acquisition_get(checkpoint_id)['status'] != 'complete'


def test_foreign_placement_never_overwritten_by_resume(case, tmp_path):
    from cifereca.providers import ProviderConfigurationError
    acquired = case.service.acquire_direct_item(case.item)
    case.service.storage.placement_put({'path':str(acquired.path),'identity_id':'alex',
        'provider_id':'numilog','source_item_id':'other-purchase','artifact_id':None,'state':'present'})
    with pytest.raises(ProviderConfigurationError):
        case.service.resume_numilog_materialization(acquired.metadata['numilog_checkpoint_id'])
    placement = case.service.storage.placement_for_path(acquired.path)
    assert placement['identity_id'] == 'alex' and placement['source_item_id'] == 'other-purchase'
    assert len(case.calls) == 1


def test_receipt_survives_new_service_without_provider_connection(case, tmp_path):
    from tests.test_sync_state_contract import _service
    acquired = case.service.acquire_direct_item(case.item)
    checkpoint_id = acquired.metadata['numilog_checkpoint_id']
    case.service.close()
    reopened = _service(tmp_path)
    try:
        assert not any(a.provider_id == 'numilog' for a in reopened.accounts)
        result = reopened.resume_numilog_materialization(checkpoint_id)
        assert result.identity_id == case.account.identity_id and result.path.is_file()
        assert reopened.storage.acquisition_get(checkpoint_id)['status'] == 'complete'
        assert case.calls == [case.item.record_id]
    finally:reopened.close()


def test_cancelled_index_keeps_receipt_and_preserves_cancellation(case, monkeypatch):
    from cifereca.plugin_api import Cancelled
    original = case.service.catalog_index_path
    def cancelled(*_a, **_k):raise Cancelled()
    monkeypatch.setattr(case.service,'catalog_index_path',cancelled)
    with pytest.raises(Cancelled):case.service.obtain_search_item(case.item)
    row=case.service.storage.acquisitions(provider_id='numilog')[0]
    assert row['status'] == 'attention_required' and row['artifact_id'] is None
    monkeypatch.setattr(case.service,'catalog_index_path',original)
    case.service.resume_numilog_materialization(row['acquisition_id'])
    assert len(case.calls) == 1


def test_source_rebound_during_checkpoint_hash_cannot_publish_receipt(case, monkeypatch):
    from cifereca.providers import ProviderConfigurationError
    import cifereca.core.numilog_materialization_checkpoint as checkpoint
    original=checkpoint._read_hash_within
    def rebound(*args):
        result=original(*args)
        case.service.storage.provider_connection_put({'provider_id':'numilog',
            'credential_id':case.account.credential_id,'identity_id':'alex'})
        return result
    monkeypatch.setattr(checkpoint,'_read_hash_within',rebound)
    with pytest.raises(ProviderConfigurationError):case.service.acquire_direct_item(case.item)
    assert case.service.storage.acquisitions(provider_id='numilog') == []


def test_checkpoint_storage_failure_is_expurgated_and_never_claims_complete(case, monkeypatch):
    from cifereca.providers import ProviderConfigurationError
    def failed(*_a,**_k):raise RuntimeError('PRIVATE ?auth=PRIVATE local path')
    monkeypatch.setattr(case.service.storage,'acquisition_put',failed)
    with pytest.raises(ProviderConfigurationError) as exc:case.service.acquire_direct_item(case.item)
    assert 'PRIVATE' not in str(exc.value)
    assert case.service.storage.acquisitions(provider_id='numilog') == []
