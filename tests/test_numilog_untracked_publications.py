"""Explicit acquisition must not redownload a readable, untracked publication."""
from pathlib import Path
import pytest
from cifereca.providers import ProviderConfigurationError
from tests.test_numilog_owned_library_download import case


@pytest.mark.parametrize('relative', ['Titre lisible - Auteur.epub','acsm-abcdef/book.epub','edition.pdf','edition.kepub'])
def test_untracked_readable_publication_blocks_before_provider_without_modification(case, relative):
    root = case.service.library_identity_provider_dir(case.account.identity_id,'numilog')
    orphan = root/relative
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b'UNTRACKED ORIGINAL BYTES')
    with pytest.raises(ProviderConfigurationError, match='rapprochement'):
        case.service.acquire_direct_item(case.item)
    assert case.calls == [] and case.inventory == []
    assert orphan.read_bytes() == b'UNTRACKED ORIGINAL BYTES'
    assert case.service.storage.acquisitions(provider_id='numilog') == []


def test_known_complete_or_pending_receipt_does_not_block_another_exact_purchase(case):
    from tests.test_core_p0_1862_numilog_store_mirror import _row
    from tests.test_kobo_backup_fault_isolation import _write_epub
    first = case.service.acquire_direct_item(case.item)
    # A pending, exact receipt is known provenance; a different purchase can
    # proceed without adopting or re-downloading the delivered first file.
    other = _row(case.account.credential_id, 1)
    def download(selected, directory):
        case.calls.append(selected.record_id)
        import hashlib
        path = Path(directory)/('exact-'+hashlib.sha256(selected.record_id.encode()).hexdigest()[:12]+'.epub')
        _write_epub(path)
        return path, 'https://private.invalid?auth=PRIVATE'
    case.session.download_direct = download
    second = case.service.obtain_search_item(other)
    assert second.path.is_file() and first.path.is_file()
    assert case.calls == [case.item.record_id, other.record_id]
    assert case.service.storage.acquisition_get(first.metadata['numilog_checkpoint_id'])['status'] == 'attention_required'
    # Completed placements remain known on later acquisitions too.
    case.service.resume_numilog_materialization(first.metadata['numilog_checkpoint_id'])
    third = _row(case.account.credential_id, 2)
    case.service.acquire_direct_item(third)
    assert case.calls == [case.item.record_id, other.record_id, third.record_id]


@pytest.mark.parametrize('scope', ['foreign_identity','wrong_provider','missing_artifact','wrong_format'])
def test_incomplete_or_foreign_placement_cannot_hide_an_orphan(case, scope):
    import hashlib
    from tests.test_core_p0_1862_numilog_store_mirror import _row
    storage = case.service.storage
    root = case.service.library_identity_provider_dir(case.account.identity_id,'numilog')
    path = root/'untracked.epub';data=b'UNCHANGED ORIGINAL';path.write_bytes(data)
    sha=hashlib.sha256(data).hexdigest();aid='sha256:'+sha
    if scope != 'missing_artifact':storage.artifact_put({'artifact_id':aid,'sha256':sha,'size':len(data),
        'media_format':'unknown' if scope == 'wrong_format' else 'epub','kind':'file'})
    storage.placement_put({'path':str(path),'identity_id':'alex' if scope == 'foreign_identity' else case.account.identity_id,
        'provider_id':'local' if scope == 'wrong_provider' else 'numilog','source_item_id':_row(case.account.credential_id,1).record_id,
        'artifact_id':aid,'state':'present'})
    with pytest.raises(ProviderConfigurationError):case.service.acquire_direct_item(case.item)
    assert case.calls == [] and path.read_bytes() == data


@pytest.mark.parametrize('kind', ['file_symlink','directory_symlink','root_ancestor_symlink'])
def test_no_follow_traversal_refuses_aliases_before_provider(case, tmp_path, kind):
    root = case.service.library_identity_provider_dir(case.account.identity_id,'numilog')
    outside = tmp_path/'outside';outside.mkdir();(outside/'private.epub').write_bytes(b'KEEP OUTSIDE')
    if kind == 'file_symlink':(root/'alias.epub').symlink_to(outside/'private.epub')
    if kind == 'directory_symlink':(root/'alias').symlink_to(outside,target_is_directory=True)
    if kind == 'root_ancestor_symlink':
        # Keep the immediate identity/provider components ordinary; only the
        # configured library ancestor is an alias, beyond the old is_symlink checks.
        alias = tmp_path/'library-alias';alias.symlink_to(root.parent.parent,target_is_directory=True)
        case.service.config.set('library.directory',str(alias))
    with pytest.raises(ProviderConfigurationError):case.service.acquire_direct_item(case.item)
    assert case.calls == [] and (outside/'private.epub').read_bytes() == b'KEEP OUTSIDE'


@pytest.mark.parametrize('budget', ['entries','depth'])
def test_inspection_budget_failure_is_fixed_and_never_downloads(case, monkeypatch, budget):
    import cifereca.core.numilog_publication_guard as guard
    root = case.service.library_identity_provider_dir(case.account.identity_id,'numilog')
    if budget == 'entries':
        monkeypatch.setattr(guard,'MAX_ENTRIES',2)
        for i in range(3):(root/f'harmless-{i}.txt').write_text('data')
    else:
        monkeypatch.setattr(guard,'MAX_DEPTH',1)
        (root/'one'/'two').mkdir(parents=True)
    with pytest.raises(ProviderConfigurationError) as error:case.service.acquire_direct_item(case.item)
    assert 'harmless' not in str(error.value) and str(root) not in str(error.value)
    assert case.calls == []


def test_guard_is_not_called_during_passive_target_render(case, monkeypatch):
    import cifereca.core.numilog_publication_guard as guard
    monkeypatch.setattr(guard,'require_tracked_publications',lambda *_a:pytest.fail('render must not inspect files'))
    assert case.service.owned_library_download_target(case.entry) is not None
    assert not case.calls


def test_revoked_right_during_inspection_is_rechecked_before_provider(case, monkeypatch):
    import cifereca.core.numilog_publication_guard as guard
    from cifereca.core.owned_library_download import OwnedDownloadRejected
    target = case.service.owned_library_download_target(case.entry)
    original = guard.require_tracked_publications
    def revoke(*args):
        original(*args)
        case.service.storage.entitlement_put({**case.right,'state':'revoked'})
    monkeypatch.setattr(guard,'require_tracked_publications',revoke)
    with pytest.raises(OwnedDownloadRejected):case.service.obtain_owned_library_item(target)
    assert case.calls == []


@pytest.mark.parametrize('credential', ['same', 'sibling-account'])
def test_attested_legacy_placement_does_not_require_a_new_receipt(case, credential):
    import hashlib
    from tests.test_core_p0_1862_numilog_store_mirror import _row
    storage = case.service.storage
    root = case.service.library_identity_provider_dir(case.account.identity_id,'numilog')
    path=root/'legacy-known.epub';data=b'LEGACY KEPT BYTES';path.write_bytes(data)
    sha=hashlib.sha256(data).hexdigest();aid='sha256:'+sha
    storage.artifact_put({'artifact_id':aid,'sha256':sha,'size':len(data),'media_format':'epub','kind':'file'})
    storage.placement_put({'path':str(path),'identity_id':case.account.identity_id,'provider_id':'numilog',
        'source_item_id':_row(case.account.credential_id if credential == 'same' else credential,1).record_id,
        'artifact_id':aid,'state':'present'})
    result=case.service.obtain_search_item(case.item)
    assert result.indexed['status'] == 'indexed'
    assert case.calls == [case.item.record_id] and path.read_bytes() == data


@pytest.mark.parametrize('cancel', [False, True])
def test_inspection_errors_are_expurgated_and_cancellation_is_preserved(case, monkeypatch, cancel):
    import cifereca.core.numilog_publication_guard as guard
    from cifereca.plugin_api import Cancelled
    def failure(*_a):
        if cancel:raise Cancelled()
        raise OSError('PRIVATE provider path ?auth=PRIVATE')
    monkeypatch.setattr(guard.os,'scandir',failure)
    with pytest.raises(Cancelled if cancel else ProviderConfigurationError) as error:
        case.service.acquire_direct_item(case.item)
    assert 'PRIVATE' not in str(error.value) and case.calls == []


@pytest.mark.parametrize('relative', ['Titre lisible.epub','acsm-abcdef/book.epub'])
def test_process_crash_before_receipt_blocks_next_attempt_without_redownload(tmp_path, monkeypatch, relative):
    import os
    import subprocess
    import sys
    from tests.test_sync_state_contract import _service
    from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    script='''
import os,sys
from pathlib import Path
from tests.test_sync_state_contract import _service
from tests.test_core_p0_1862_numilog_store_mirror import _connect,_row
from tests.test_kobo_backup_fault_isolation import _write_epub
root=Path(sys.argv[1]); service=_service(root)
account,session,_=_connect(service,number=0)
def download(item,directory):
    path=Path(directory)/sys.argv[2];_write_epub(path)
    with (root/'provider-calls').open('a') as out:
        out.write('one publication\\n');out.flush();os.fsync(out.fileno())
    os._exit(73)
session.download_direct=download
service.acquire_direct_item(_row(account.credential_id,0))
'''
    child=subprocess.run([sys.executable,'-c',script,str(tmp_path),relative],
        cwd=Path(__file__).resolve().parents[1],env=os.environ.copy(),capture_output=True,text=True,timeout=20)
    assert child.returncode == 73, child.stderr
    service=_service(tmp_path)
    try:
        account,session,inventory=_connect(service,number=0)
        orphan=service.library_identity_provider_dir(account.identity_id,'numilog')/relative
        before=orphan.read_bytes()
        session.download_direct=lambda *_a:pytest.fail('a crash residue must prevent another GET')
        assert service.storage.acquisitions(provider_id='numilog') == []
        with pytest.raises(ProviderConfigurationError,match='rapprochement'):
            service.acquire_direct_item(_row(account.credential_id,0))
        assert (tmp_path/'provider-calls').read_text().splitlines() == ['one publication']
        assert orphan.read_bytes() == before and inventory == []
        assert service.storage.acquisitions(provider_id='numilog') == []
    finally:service.close()


def test_other_identity_untracked_book_is_not_inspected_or_adopted(case):
    root=case.service.library_identity_provider_dir('alex','numilog')
    foreign=root/'Other owner.epub';foreign.write_bytes(b'OTHER OWNER KEPT')
    result=case.service.obtain_search_item(case.item)
    assert result.identity_id == case.account.identity_id
    assert case.calls == [case.item.record_id] and foreign.read_bytes() == b'OTHER OWNER KEPT'
    assert case.service.storage.placement_for_path(foreign) is None
