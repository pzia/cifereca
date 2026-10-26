"""Known guard refusals remain distinct from acquired loans in sync summaries."""
from types import SimpleNamespace as NS
import pytest
from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from cifereca.models import Account, Loan
from tests.test_sync_state_contract import _service
from tests.test_core_ui_1132_opening_sync_close_gate import _method, _host


@pytest.mark.parametrize('count', [0, 1, 4])
@pytest.mark.parametrize('guard', ['borrower', 'cross_identity'])
def test_real_guards_are_counted_and_persisted_without_fulfillment(tmp_path, monkeypatch, count, guard):
    service = _service(tmp_path)
    try:
        account = Account(provider_id='paris', credential_id='source-a', unique_id='borrower-a',
                          identity_id='default', is_primary=guard == 'cross_identity')
        service.accounts = [account]
        service.storage.provider_connection_put({'provider_id': 'paris', 'credential_id': 'source-a', 'identity_id': 'default'})
        loans = [Loan(id=f'loan-{i}', record_id=f'edition-{i}', provider='PNB', medium='digital',
                      title='Synthetic', source_provider='paris', source_credential='source-a',
                      source_account_id='borrower-a', identity_id='default', raw={'synthetic': True})
                 for i in range(count)]
        if guard == 'cross_identity':
            for loan in loans:
                service.storage.acquisition_put({'acquisition_id': 'prior-'+loan.id,
                    'provider_id': 'paris', 'credential_id': 'source-b', 'account_id': 'borrower-b',
                    'identity_id': 'alex', 'item_id': loan.record_id, 'status': 'complete',
                    'strategy': 'loan_auto', 'metadata': {'provider_loan_id': loan.id}})
        monkeypatch.setattr(service, '_loans_for_account', lambda *_a, **_kw: loans)
        monkeypatch.setattr(service, '_kobo_adapter', lambda: NS(available=lambda: False))
        monkeypatch.setattr(service, 'mirror_personal_libraries', lambda: {})
        monkeypatch.setattr(service, 'acquire_digital_loan', lambda *_a, **_kw: pytest.fail('guard cannot fetch ACSM'))
        monkeypatch.setattr(service, 'execute_acquisition_request', lambda *_a: pytest.fail('guard cannot fulfill'))
        events = []
        enable_synthetic_paris_opening(service)
        summary = service.opening_sync(progress=events.append)
        rows = {r['identity_id']: r for r in summary['identities']}
        first, sibling = rows['default'], rows['alex']
        expected = {'skipped_borrower_binding': count if guard == 'borrower' else 0,
                    'skipped_cross_identity': count if guard == 'cross_identity' else 0}
        for key, value in expected.items():
            assert first[key] == value
            assert service.storage.latest_sync_run('default')['detail'][key] == value
            assert sibling[key] == 0
            ended = next(e for e in events if e['event'] == 'identity_end' and e['identity_id'] == 'default')
            assert ended[key] == value
        assert first['new_acquisitions'] == 0 and first['attention_required'] == count
        assert first['status'] == 'partial'  # v1 coverage is never complete
        assert sibling['status'] == 'success'
        assert len(service.storage.acquisitions()) == (count if guard == 'cross_identity' else 0)
    finally:
        service.close()


def test_completion_message_names_guard_reasons_without_private_metadata():
    start, _ = _method()
    host, tasks, _effects = _host()
    feedback = []
    host._activity_result = lambda message, **kwargs: feedback.append((message, kwargs))
    start(host)
    tasks[0][0][2]({'identities': [
        {'new_acquisitions': 0, 'attention_required': 4, 'errors': 0,
         'skipped_borrower_binding': 4, 'identity_id': 'PRIVATE-A'},
        {'new_acquisitions': 0, 'attention_required': 4, 'errors': 0,
         'skipped_cross_identity': 4, 'code': 'PRIVATE-B'},
    ]})
    message, flags = feedback[0]
    assert '0 acquisition(s)' in message and '8 action(s)' in message
    assert '4 association(s) emprunteur à configurer' in message
    assert '4 conflit(s) entre identités' in message
    assert flags == {'success': False, 'persistent': True}
    assert 'PRIVATE' not in message


@pytest.mark.parametrize('invalid', ['PRIVATE', True, -1, 1_000_001, None])
def test_ui_rejects_malformed_guard_counts(invalid):
    start, _ = _method()
    host, tasks, _effects = _host()
    feedback = []
    host._activity_result = lambda message, **kwargs: feedback.append((message, kwargs))
    start(host)
    tasks[0][0][2]({'identities': [{'new_acquisitions': 0, 'attention_required': 1, 'errors': 0,
                                'skipped_borrower_binding': invalid, 'skipped_cross_identity': invalid}]})
    message, flags = feedback[0]
    assert message == 'Synchronisation : 0 acquisition(s), 1 action(s), 0 erreur(s)'
    assert flags == {'success': False, 'persistent': True}


def test_two_identity_field_scenario_reports_eight_refusals_without_acquisition(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        service.accounts = [Account(provider_id='paris', credential_id='source-'+iid,
            unique_id='borrower-'+iid, identity_id=iid, is_primary=iid == 'alex')
            for iid in ('default', 'alex')]
        for account in service.accounts:
            service.storage.provider_connection_put({'provider_id': 'paris',
                'credential_id': account.credential_id, 'identity_id': account.identity_id})
        def loans(account, **_kw):
            return [Loan(id=f'shared-{i}', record_id=f'edition-{i}', provider='PNB', medium='digital',
                title='PRIVATE-TITLE', source_provider='paris', source_credential=account.credential_id,
                source_account_id=account.unique_id, identity_id=account.identity_id, raw={'synthetic': True})
                for i in range(4)]
        monkeypatch.setattr(service, '_loans_for_account', loans)
        monkeypatch.setattr(service, '_kobo_adapter', lambda: NS(available=lambda: False))
        monkeypatch.setattr(service, 'mirror_personal_libraries', lambda: {})
        monkeypatch.setattr(service, 'acquire_digital_loan', lambda *_a, **_kw: pytest.fail('refusal must prevent ACSM acquisition'))
        monkeypatch.setattr(service, 'execute_acquisition_request', lambda *_a: pytest.fail('refusal must prevent fulfillment'))
        enable_synthetic_paris_opening(service)
        summary = service.opening_sync()
        rows = {r['identity_id']: r for r in summary['identities']}
        assert rows['default']['skipped_borrower_binding'] == 4
        assert rows['default']['skipped_cross_identity'] == 0
        assert rows['alex']['skipped_borrower_binding'] == 0
        assert rows['alex']['skipped_cross_identity'] == 4
        assert all(r['attention_required'] == 4 and r['new_acquisitions'] == 0 and r['status'] == 'partial'
                   for r in rows.values())
        assert service.storage.acquisitions() == [] and service.storage.artifacts() == []
        start, _ = _method()
        host, tasks, _ = _host()
        feedback = []
        host._activity_result = lambda message, **flags: feedback.append((message, flags))
        start(host)
        tasks[0][0][2](summary)
        message, flags = feedback[0]
        assert '0 acquisition(s)' in message and '8 action(s)' in message
        assert '4 association(s) emprunteur à configurer' in message
        assert '4 conflit(s) entre identités' in message and 'PRIVATE' not in message
        assert flags == {'success': False, 'persistent': True}
    finally:
        service.close()


@pytest.mark.parametrize(('status', 'code'), [
    ('retryable_error', 'adept_loan_identity_conflict'),
    ('unchanged', 'adept_borrower_binding_required'),
    ('attention_required', 'adept_profile_missing'),
    ('attention_required', 'adept_loan_identity_conflict-extra'),
])
def test_only_exact_attention_guard_codes_contribute(tmp_path, monkeypatch, status, code):
    service = _service(tmp_path)
    try:
        service.accounts = [Account(provider_id='paris', credential_id='source-a', unique_id='borrower-a', identity_id='default')]
        loan = Loan(id='loan', record_id='edition', provider='PNB', medium='digital', title='Synthetic',
                    source_provider='paris', source_credential='source-a', source_account_id='borrower-a', raw={'synthetic': True})
        monkeypatch.setattr(service, '_loans_for_account', lambda *_a, **_kw: [loan])
        monkeypatch.setattr(service, '_kobo_adapter', lambda: NS(available=lambda: False))
        monkeypatch.setattr(service, 'mirror_personal_libraries', lambda: {})
        monkeypatch.setattr(service, '_acquire_active_loan_automatically', lambda _: {'status': status, 'code': code})
        enable_synthetic_paris_opening(service)
        rows = service.opening_sync()['identities']
        assert all(r['skipped_borrower_binding'] == r['skipped_cross_identity'] == 0 for r in rows)
    finally:
        service.close()
