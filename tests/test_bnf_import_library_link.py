"""Manual BnF imports retain an exact display link, never a provider receipt."""
from pathlib import Path
from dataclasses import replace
import zipfile

import pytest
import cifereca.services as facade
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


@pytest.fixture
def bnf_importer(connected_core, tmp_path, monkeypatch, request):
    service, session, account, loan, state, downloaded = connected_core
    monkeypatch.setattr(facade, 'data_dir', lambda: tmp_path / 'data')
    monkeypatch.setattr(service, 'resolve_adept_profile_for_identity', lambda _: ('synthetic', 'identity'))
    monkeypatch.setattr(service, '_originating_adept_profile_id', lambda *a: None)
    requests = []
    media_format = getattr(request, 'param', 'pdf')
    def fulfill(request):
        requests.append(request)
        path = Path(request['destination'])
        if media_format == 'epub':
            with zipfile.ZipFile(path, 'w') as book:
                book.writestr('mimetype', 'application/epub+zip')
                book.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
                book.writestr('book.opf', '<package/>')
                book.writestr('body.txt', str(len(requests)))
        else:
            path.write_bytes(b'%PDF-1.4\n% synthetic document ' + str(len(requests)).encode() + b'\n%%EOF\n')
        return {'ok': True, 'artifact': {'path': str(path)}}
    monkeypatch.setattr(service, 'execute_acquisition_request', fulfill)
    yield service, loan, requests


@pytest.mark.parametrize('bnf_importer', ['pdf', 'epub'], indirect=True)
def test_bnf_import_and_observed_loan_have_one_library_row(bnf_importer, request):
    service, loan, requests = bnf_importer
    service._persist_active_loans('default', [loan])
    source = Path(service.stage_bnf_loan_acsm(
        loan, confirmed=True, expected_identity_id='default')['path'])
    rights = service.storage.entitlements()
    result = service.import_bnf_loan_acsm(
        source, expected_identity_id='default', loan=loan, title=loan.title)
    assert result['status'] == 'complete'
    assert Path(result['path']).suffix == '.' + request.node.callspec.params['bnf_importer']
    rows = service.library_entries(identity_id='default', scan=False)
    assert len(rows) == 1
    assert rows[0]['local'] is True
    assert rows[0]['loan_id'] == loan.id
    assert rows[0]['entitlement_type'] == 'loan'
    assert service.storage.entitlements() == rights
    assert requests[0]['source']['provider'] == 'manual'
    ledger, = service.storage.acquisitions()
    assert ledger['provider_id'] == 'manual' and not ledger['entitlement_id']
    assert '/bnf-pret/acsm-' in result['path']
    again = service.import_bnf_loan_acsm(
        source, expected_identity_id='default', loan=loan, title=loan.title)
    assert again['status'] == 'unchanged' and len(requests) == 1
    service.catalog_scan()
    assert len(service.library_entries(identity_id='default', scan=False)) == 1


def staged(service, loan, payload=b'<fulfillment><format>application/pdf</format></fulfillment>'):
    directory = service._acquisition_work_dir(loan.identity_id, 'bnf-pret', loan.id)
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / 'bnf-loan-synthetic.acsm'
    source.write_bytes(payload)
    return source


def imported(service, loan):
    return service.import_bnf_loan_acsm(staged(service, loan),
        expected_identity_id=loan.identity_id, loan=loan, title=loan.title)


@pytest.mark.parametrize('difference', ['identity', 'credential', 'account', 'loan', 'edition'])
def test_link_never_matches_another_borrower_or_loan(bnf_importer, difference):
    service, loan, requests = bnf_importer
    other = replace(loan)
    if difference == 'identity':
        other.identity_id = 'alex'
    elif difference == 'credential':
        other.source_credential = 'other-card'
    elif difference == 'account':
        other.source_account_id = 'other-reader'
    elif difference == 'loan':
        other.id = 'another-loan'
    else:
        other.record_id = 'another-edition'
    service._persist_active_loans(other.identity_id, [other])
    imported(service, loan)
    rights = service.storage.entitlements()
    rows = service.library_entries(identity_id=other.identity_id, scan=False)
    remote, = [r for r in rows if not r['local']]
    assert remote['loan_id'] == other.id
    assert service.storage.entitlements() == rights
    if difference == 'identity':
        assert len(rows) == 1


def test_two_profiles_and_homonymous_loans_remain_distinct(bnf_importer):
    service, loan, requests = bnf_importer
    other_account = replace(service.accounts[0], unique_id='reader-b',
                            credential_id='card-b', identity_id='alex')
    service.accounts.append(other_account)
    service.storage.provider_connection_put({
        'identity_id': 'alex', 'provider_id': 'bnf-pret',
        'credential_id': 'card-b', 'label': 'Synthetic B', 'enabled': True, 'metadata': {}})
    other = replace(loan, identity_id='alex', source_credential='card-b', source_account_id='reader-b')
    service._persist_active_loans('default', [loan])
    service._persist_active_loans('alex', [other])
    first = imported(service, loan)
    service.current_identity = service.identity_by_id('alex')
    second = imported(service, other)
    assert first['path'] != second['path']
    assert len(requests) == 2
    for identity, result in [('default', first), ('alex', second)]:
        rows = service.library_entries(identity_id=identity, scan=True)
        assert len(rows) == 1 and rows[0]['local_path'] == result['path']
        assert rows[0]['identity_id'] == identity
    assert {r['provider_id'] for r in service.storage.acquisitions()} == {'manual'}
    assert len(service.storage.entitlements()) == 2


def test_binding_is_durable_after_restart(bnf_importer):
    from cifereca.services import LibraryService
    from cifereca.storage import Storage
    from cifereca.diagnostics import Diagnostics
    service, loan, requests = bnf_importer
    service._persist_active_loans('default', [loan])
    result = imported(service, loan)
    database, config = service.storage.path, service.config
    service.close()
    reopened = LibraryService(config, Storage(database), Diagnostics())
    try:
        row, = reopened.library_entries(identity_id='default', scan=True)
        assert row['local_path'] == result['path'] and row['loan_id'] == loan.id
        assert row['entitlement_type'] == 'loan'
    finally:
        reopened.close()


def test_existing_manual_file_is_not_moved_or_given_bnf_provenance(bnf_importer):
    service, loan, requests = bnf_importer
    source = staged(service, loan)
    first = service.import_acsm(source, identity_id='default', title=loan.title)
    service._persist_active_loans('default', [loan])
    second = service.import_bnf_loan_acsm(source, expected_identity_id='default', loan=loan)
    assert second['path'] == first['path'] and '/manual/acsm-' in first['path']
    assert second['status'] == 'unchanged' and len(requests) == 1
    ledger, = service.storage.acquisitions()
    assert 'bnf_observed_loan' not in ledger['metadata']
    assert len(service.library_entries(identity_id='default', scan=True)) == 2


@pytest.mark.parametrize('corruption', ['invalid', 'different_pdf', 'symlink'])
def test_reimport_revalidates_existing_bytes_without_new_fulfillment(bnf_importer, corruption):
    from cifereca.core.diagnostic_bnf_loan_action import BnfLoanActionError
    service, loan, requests = bnf_importer
    result = imported(service, loan)
    path = Path(result['path'])
    if corruption == 'symlink':
        target = path.with_name('other.pdf')
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    else:
        path.write_bytes(b'broken' if corruption == 'invalid' else b'%PDF-1.4\n% changed\n%%EOF\n')
    with pytest.raises(BnfLoanActionError, match='bnf_import_failed'):
        imported(service, loan)
    assert len(requests) == 1


def test_same_token_cannot_overwrite_its_first_observed_borrower(bnf_importer):
    service, loan, requests = bnf_importer
    result = imported(service, loan)
    service.accounts.append(replace(service.accounts[0], unique_id='another-reader'))
    other = replace(loan, source_account_id='another-reader', id='loan-b')
    service._persist_active_loans('default', [other])
    imported(service, other)
    ledger, = service.storage.acquisitions()
    assert ledger['metadata']['bnf_observed_loan']['account_id'] == loan.source_account_id
    assert len(requests) == 1
    assert len(service.library_entries(identity_id='default', scan=False)) == 2


@pytest.mark.parametrize('alteration', ['status', 'identity', 'artifact', 'path', 'schema', 'placement'])
def test_projection_refuses_inconsistent_import_evidence(bnf_importer, alteration):
    service, loan, requests = bnf_importer
    service._persist_active_loans('default', [loan])
    result = imported(service, loan)
    row, = service.storage.acquisitions()
    if alteration == 'status':
        service.storage.acquisition_update_state(row['acquisition_id'], 'retryable_error')
    elif alteration == 'path':
        service.storage.acquisition_update_state(row['acquisition_id'], 'complete', metadata={'path': '/unrelated.pdf'})
    elif alteration in {'identity', 'schema'}:
        scope = row['metadata']['bnf_observed_loan']
        scope['identity_id' if alteration == 'identity' else 'unknown'] = 'another'
        service.storage.acquisition_update_state(row['acquisition_id'], 'complete', metadata={'bnf_observed_loan': scope})
    elif alteration == 'artifact':
        service.storage.acquisition_update_state(row['acquisition_id'], 'complete', artifact_id='sha256:other')
    else:
        placement = service.storage.placement_for_path(Path(result['path']))
        placement['provider_id'] = 'manual'
        service.storage.placement_put(placement)
    rows = service.library_entries(identity_id='default', scan=False)
    assert len(rows) == 2
    assert len([r for r in rows if not r['local']]) == 1


def test_projection_has_no_filesystem_or_provider_side_effects(bnf_importer, monkeypatch):
    service, loan, requests = bnf_importer
    service._persist_active_loans('default', [loan])
    imported(service, loan)
    def forbidden(*args, **kwargs):
        pytest.fail('display must only read SQLite')
    monkeypatch.setattr(service, 'catalog_scan', forbidden)
    monkeypatch.setattr(service, 'execute_acquisition_request', forbidden)
    monkeypatch.setattr(Path, 'open', forbidden)
    rows = service.library_entries(identity_id='default', scan=False)
    assert len(rows) == 1


def test_import_without_observed_right_never_creates_one(bnf_importer):
    service, loan, requests = bnf_importer
    imported(service, loan)
    assert service.storage.entitlements() == []
    row, = service.library_entries(identity_id='default', scan=False)
    assert row['local'] and row['possessions'] == [] and row['entitlement_type'] is None


def test_two_accounts_with_homonymous_editions_get_their_exact_file(bnf_importer):
    service, loan, requests = bnf_importer
    other_account = replace(service.accounts[0], unique_id='reader-b', credential_id='card-b')
    service.accounts.append(other_account)
    service.storage.provider_connection_put({
        'identity_id': 'default', 'provider_id': 'bnf-pret',
        'credential_id': 'card-b', 'label': 'Synthetic B', 'enabled': True, 'metadata': {}})
    other = replace(loan, source_credential='card-b', source_account_id='reader-b',
                    id='loan-b', record_id='book-b')
    service._persist_active_loans('default', [loan, other])
    first = imported(service, loan)
    second = service.import_bnf_loan_acsm(
        staged(service, other, b'<fulfillment><id>other</id><format>application/pdf</format></fulfillment>'),
        expected_identity_id='default', loan=other, title=loan.title)
    rows = service.library_entries(identity_id='default', scan=True)
    assert len(rows) == 2
    assert {r['loan_id']: r['local_path'] for r in rows} == {
        loan.id: first['path'], other.id: second['path']}


def test_two_storage_connections_cannot_replace_first_binding(bnf_importer):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from cifereca.storage import Storage
    from cifereca.core.bnf_import_provenance import observed_loan
    service, loan, requests = bnf_importer
    result = service.import_acsm(staged(service, loan), identity_id='default',
                                 placement_provider_id='bnf-pret')
    other = Storage(service.storage.path)
    scopes = [observed_loan(loan), observed_loan(replace(loan, source_account_id='other-reader'))]
    ready = Barrier(2)
    def bind(storage, scope):
        ready.wait(timeout=5)
        return storage.acquisition_bind_bnf_display_loan(
            result['acquisition_id'], path=result['path'], artifact_id=result['artifact_id'], scope=scope)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(bind, storage, scope)
                       for storage, scope in zip((service.storage, other), scopes)]
            assert sorted(f.result(timeout=5) for f in futures) == [False, True]
        saved = service.storage.acquisition_get(result['acquisition_id'])['metadata']['bnf_observed_loan']
        assert saved in scopes
    finally:
        other.close()
