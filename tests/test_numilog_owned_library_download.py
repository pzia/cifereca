"""Real Core storage/materialization; synthetic explicit Numilog download only."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from cifereca.core.owned_library_download import OwnedDownloadRejected
from tests.test_sync_state_contract import _service
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_kobo_backup_fault_isolation import _write_epub


@pytest.fixture
def case(tmp_path, monkeypatch):
    service = _service(tmp_path)
    account, session, inventory = _connect(service, number=1)
    item = _row(account.credential_id, 0)
    right = {'entitlement_id':'numilog-owned-right', 'provider_id':'numilog',
             'identity_id':account.identity_id, 'credential_id':account.credential_id,
             'account_id':account.unique_id, 'item_id':item.record_id,
             'entitlement_type':'owned', 'state':'active',
             'metadata':{'source':'numilog.authenticated_library', 'title':'Fictional book'}}
    service.storage.entitlement_put(right)
    entry = {'local':False, 'possessions':[{'identity_id':account.identity_id,
        'provider_id':'numilog', 'kind':'owned', 'state':'active',
        'item_id':item.record_id, 'entitlement_id':right['entitlement_id']}]}
    calls = []
    def download(selected, directory):
        calls.append(selected.record_id)
        assert selected.record_id == item.record_id and selected.download_url is None
        path = Path(directory)/'fictional.epub';_write_epub(path)
        return path, 'https://private.invalid?auth=PRIVATE'
    session.download_direct = download
    monkeypatch.setattr(service.acquisition, 'run_post_download', lambda *_a: None)
    yield NS(service=service, account=account, session=session, entry=entry,
             right=right, calls=calls, inventory=inventory, item=item)
    service.close()


def test_passive_target_then_one_exact_owned_acquisition_indexed_for_pinned_owner(case):
    target = case.service.owned_library_download_target(case.entry)
    assert target is not None and target.provider_id == 'numilog'
    assert case.calls == [] and case.inventory == []
    assert case.item.record_id not in repr(target) and case.account.credential_id not in repr(target)
    result = case.service.obtain_owned_library_item(target)
    assert result.path.is_file() and result.indexed['status'] == 'indexed'
    assert result.identity_id == case.account.identity_id
    assert case.calls == [case.item.record_id]
    assert result.acquisition.source_url is None
    assert case.service.owned_library_download_target(case.entry) is None
    with pytest.raises(OwnedDownloadRejected):case.service.obtain_owned_library_item(target)
    assert len(case.calls) == 1


@pytest.mark.parametrize('field,value', [('state','revoked'),('account_id','other'),
    ('identity_id','other'),('entitlement_type','loan'),('loan_id','loan'),('expires_at','2030-01-01')])
def test_stale_entitlement_cannot_acquire_another_target(case, field, value):
    target = case.service.owned_library_download_target(case.entry)
    with case.service.storage._lock, case.service.storage._connect() as db:
        db.execute(f'UPDATE entitlements SET {field}=? WHERE entitlement_id=?', (value,case.right['entitlement_id']))
    with pytest.raises(OwnedDownloadRejected):case.service.obtain_owned_library_item(target)
    assert case.calls == []


@pytest.mark.parametrize('mutation', ['identity','directory','session','migration','ledger','ambiguous','public_proof'])
def test_stale_source_configuration_and_local_conflicts_fail_closed(case, mutation):
    target = case.service.owned_library_download_target(case.entry)
    assert target is not None
    if mutation == 'identity':case.service.current_identity = NS(identity_id='another')
    if mutation == 'directory':case.service.config.set('library.directory','/different-library')
    if mutation == 'session':
        case.service._sessions[case.service._session_key('numilog',case.account.credential_id)] = NS(**vars(case.session))
    if mutation == 'migration':case.service.storage.metadata_set('identity-directory-migration-v1','pending')
    if mutation == 'ledger':case.service.storage.acquisition_put({
        'acquisition_id':'existing', 'provider_id':'numilog','credential_id':case.account.credential_id,
        'account_id':case.account.unique_id,'identity_id':case.account.identity_id,
        'item_id':case.item.record_id,'status':'retryable_error'})
    if mutation == 'ambiguous':case.service.storage.entitlement_put({**case.right,'entitlement_id':'duplicate'})
    if mutation == 'public_proof':case.service.storage.entitlement_put({**case.right,'metadata':{'source':'public_catalog'}})
    with pytest.raises(OwnedDownloadRejected):case.service.obtain_owned_library_item(target)
    assert case.calls == []


def test_revoke_inside_acquisition_lock_prevents_provider_io(case, monkeypatch):
    from cifereca import services
    from cifereca.locking import InterProcessLock
    target = case.service.owned_library_download_target(case.entry)
    class Revoke(InterProcessLock):
        def acquire(self):
            super().acquire();case.service.storage.entitlement_put({**case.right,'state':'revoked'})
    monkeypatch.setattr(services,'InterProcessLock',Revoke)
    with pytest.raises(OwnedDownloadRejected):case.service.obtain_owned_library_item(target)
    assert case.calls == []


def test_local_or_ambiguous_provider_possessions_never_offer_target(case):
    for changes in [{'local':True},{'local_path':'private'},
                    {'possessions':case.entry['possessions']*2},
                    {'possessions':[*case.entry['possessions'],{**case.entry['possessions'][0],'provider_id':'kobo'}]}]:
        assert case.service.owned_library_download_target({**case.entry,**changes}) is None
    assert case.calls == [] and case.inventory == []



def test_passive_display_does_not_create_directories_or_mutate_storage(case, tmp_path):
    before_paths = set(tmp_path.rglob('*'))
    before_rights = case.service.storage.entitlements(provider_id='numilog')
    assert case.service.owned_library_download_target(case.entry) is not None
    assert set(tmp_path.rglob('*')) == before_paths
    assert case.service.storage.entitlements(provider_id='numilog') == before_rights
    assert case.calls == [] and case.inventory == []


def test_provider_refusal_of_missing_fresh_purchase_never_indexes_cached_right(case, monkeypatch):
    target = case.service.owned_library_download_target(case.entry)
    indexed = []
    monkeypatch.setattr(case.service, 'catalog_index_path', lambda *_a, **kw: indexed.append(kw))
    def refused(*_a):raise RuntimeError('fresh purchase absent ?auth=PRIVATE')
    case.session.download_direct = refused
    with pytest.raises(OwnedDownloadRejected) as exc:case.service.obtain_owned_library_item(target)
    assert indexed == [] and 'PRIVATE' not in str(exc.value)



@pytest.mark.parametrize('key,value', [('downloadable',False),('downloadable','yes'),
                                      ('owned',False),('archived',True)])
def test_contradictory_cached_metadata_is_not_admission(case, key, value):
    case.service.storage.entitlement_put({**case.right, 'metadata':{**case.right['metadata'],key:value}})
    assert case.service.owned_library_download_target(case.entry) is None
    assert case.calls == []
