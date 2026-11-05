"""Historical origin survives reconfiguration; new fulfillment stays guarded."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import AttentionRequired
from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

from tests.test_core_0984_adept_first_paris_reconcile import setup, stub_adept
from tests.test_core_0984_adept_return_status_provenance import _acquisition
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _attest(service, acquisition_id):
    row = service.storage.acquisition_get(acquisition_id)
    metadata = deepcopy(row['metadata'])
    metadata['adept_binding'].update(inventory_status='resolved', inventory_checked_at=1234,
                                     binding_source='public_inventory', expired=False,
                                     return_verification='active_confirmed')
    service.storage.acquisition_put({**row, 'metadata': metadata})


def _reconfigure(service, change):
    if change == 'rotate':
        service.storage.adept_profile_binding_set('identity', 'bob', 'new-profile')
    elif change == 'remove_normal':
        service.storage.adept_profile_binding_set('identity', 'bob', None)
    elif change == 'revoke':
        service.storage.paris_borrower_adept_binding_delete(credential_id='alice-paris', account_id='linked-123')
    elif change == 'different_grant':
        service.bind_paris_borrower_adept(credential_id='alice-paris', account_id='linked-123',
                                         borrower_identity_id='alice', confirmed=True)


@pytest.mark.parametrize('change', ['rotate', 'remove_normal', 'revoke', 'different_grant'])
def test_attested_status_is_read_only_and_origin_wins_after_reconfiguration(tmp_path, change):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(credential_id='alice-paris', account_id='linked-123',
                                         borrower_identity_id='bob', confirmed=True)
        acq = _acquisition(service, loan)
        _attest(service, acq)
        _reconfigure(service, change)
        service._adept_return_capability = lambda: True
        service.kobo_profiles = lambda: pytest.fail('status must not query inventory')
        before = service.storage.acquisition_get(acq)
        status = service.adept_return_status_for_loan(loan)
        assert status['status'] == 'ready' and status['return_available'] is True
        assert status['profile_name'] == 'bob-cli' and status['acquisition_id'] == acq
        assert service.storage.acquisition_get(acq) == before
        _, code = service._paris_adept_identity_for_loan(loan, connection_identity_id='alice')
        if change in {'rotate', 'remove_normal', 'revoke'}:
            assert code in {'adept_borrower_profile_changed', 'adept_borrower_binding_required'}
        else:
            assert service.storage.adept_profile_binding_get('identity', 'alice') == 'alice-profile'
    finally:
        service.close()


@pytest.mark.parametrize('field,value', [
    ('inventory_status', 'ambiguous'), ('inventory_checked_at', True),
    ('inventory_checked_at', 0), ('binding_source', 'guessed'),
    ('adept_profile_id', ''), ('adept_profile_name', ''), ('adept_loan_id', ''),
    ('token_present', 'true'), ('expired', None),
    ('binding_source', {}), ('loan_state', 'unknown'), ('expired', True),
    ('token_present', False), ('return_verification', 'confirmed'),
    ('return_verification', 'pending'), ('return_verification', 'verification_failed'),
])
def test_incomplete_origin_evidence_cannot_bypass_revoked_grant(tmp_path, field, value):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        acq = _acquisition(service, loan)
        _attest(service, acq)
        row = service.storage.acquisition_get(acq)
        metadata = deepcopy(row['metadata']); metadata['adept_binding'][field] = value
        service.storage.acquisition_put({**row, 'metadata': metadata})
        service._adept_return_capability = lambda: True
        result = service.adept_return_status_for_loan(loan)
        assert result['return_available'] is False
        assert result['status'] == 'adept_borrower_binding_required'
    finally:
        service.close()


def test_retired_historical_return_cannot_mutate_or_rewrite_provenance(tmp_path):
    from cifereca.plugin_api import AttentionRequired

    service, loan, paris = setup(tmp_path)
    attest_paris_dilicom_complete(paris, credential_id="alice-paris")
    try:
        _attest(service, 'adept-01')
        _reconfigure(service, 'revoke')
        _reconfigure(service, 'rotate')
        artifact = tmp_path / 'historical.epub'
        artifact.write_bytes(b'preserved historical artifact')
        before = artifact.read_bytes()
        tracked = service.storage.acquisition_get('adept-01')
        metadata = deepcopy(tracked['metadata'])
        metadata['path'] = str(artifact)
        service.storage.acquisition_put({**tracked, 'metadata': metadata})
        untouched = service.storage.acquisition_get('adept-01')
        calls = stub_adept(service)
        for _attempt in range(2):
            with pytest.raises(AttentionRequired) as exc:
                service.return_loan_prefer_adept(loan)
            assert exc.value.code == "loan_return_removed"
        assert calls['mutations'] == 0 and paris.posts == 0
        assert artifact.read_bytes() == before
        assert service.storage.acquisition_get('adept-01') == untouched
        binding = untouched['metadata']
        assert binding['path'] == str(artifact)
        assert binding['adept_binding']['adept_profile_id'] == 'bob-profile'
        assert service.storage.adept_profile_binding_get('identity', 'bob') == 'new-profile'
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id='alice-paris', account_id='linked-123',
        ) is None
    finally:
        service.close()


@pytest.mark.parametrize('failure', ['profile_missing', 'inventory_missing', 'inventory_duplicate', 'returned_with_token'])
def test_stale_historical_proof_cannot_reopen_retired_voluntary_return(tmp_path, failure):
    service, loan, paris = setup(tmp_path)
    try:
        _attest(service, 'adept-01'); _reconfigure(service, 'revoke')
        if failure == 'profile_missing':
            service.kobo_profiles = lambda: [{'profile_id': 'new-profile', 'name': 'new-cli'}]
        row = {'id': 'adobe-loan-01', 'returned': failure == 'returned_with_token',
               'expired': False, 'token_present': True}
        inventory = [] if failure == 'inventory_missing' else ([row, row] if failure == 'inventory_duplicate' else [row])
        # Even stale or contradictory runtime evidence cannot reopen the
        # retired return path. No ADEPT inventory or profile I/O is allowed.
        before = deepcopy(service.storage.acquisition_get('adept-01'))
        service._kobo_adapter = lambda: pytest.fail('retired return must not read ADEPT')
        service.kobo_profiles = lambda: pytest.fail('retired return must not list profiles')
        service.execute_drm_mutation = lambda request: pytest.fail('no DRM mutation')
        with pytest.raises(AttentionRequired) as rejected:
            service.return_loan_prefer_adept(loan)
        assert rejected.value.code == 'loan_return_removed'
        assert paris.posts == 0
        assert service.storage.acquisition_get('adept-01') == before
    finally:
        service.close()
