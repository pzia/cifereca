"""Initial loan admission remains scoped and precedes artifacts/provider I/O."""
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from cifereca.core.loan_acquisition_admission import resolve
from cifereca.models import Account, Loan
from tests.test_sync_state_contract import _service


LOGGER = NS(info=lambda *_a, **_kw: None, warning=lambda *_a, **_kw: None)


def _setup(tmp_path, *, bound=True, linked=False):
    service = _service(tmp_path)
    account = Account(provider_id='paris', credential_id='source-a', identity_id='default',
                      unique_id='linked-a' if linked else '', is_primary=not linked)
    service.accounts = [account]
    if bound:
        service.storage.provider_connection_put({'provider_id': 'paris', 'credential_id': 'source-a', 'identity_id': 'default'})
    loan = Loan(id='loan-0', record_id='edition-0', provider='PNB', medium='digital', title='Synthetic',
                source_provider='paris', source_credential='source-a',
                source_account_id='linked-a' if linked else 'primary', identity_id='default')
    service.current_identity = service.identity_by_id('alex')
    return service, loan


def _forbid_work(service, monkeypatch):
    def forbidden(*_a, **_kw): pytest.fail('initial admission cannot mutate, inspect artifacts or contact a provider')
    for name in ('acquire_digital_loan', 'execute_acquisition_request', '_kobo_adapter', 'catalog_index_path'):
        monkeypatch.setattr(service, name, forbidden)
    for name in ('catalog_entries', 'acquisition_put', 'entitlement_put'):
        monkeypatch.setattr(service.storage, name, forbidden)


@pytest.mark.parametrize('count', [0, 1, 4])
def test_admitted_context_uses_connection_owner_and_existing_right_only(tmp_path, monkeypatch, count):
    service, loan = _setup(tmp_path)
    try:
        if count:
            service._persist_active_loans('default', [replace(loan, id=f'loan-{i}', record_id=f'edition-{i}')
                                                     for i in range(count)])
        before = service.storage.entitlements(provider_id='paris', state='active')
        expected = next((r['entitlement_id'] for r in before if r['loan_id'] == loan.id), None)
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal is None and result.identity.identity_id == 'default'
        assert result.entitlement_id == expected
        assert service.current_identity.identity_id == 'alex'
        assert service.storage.entitlements(provider_id='paris', state='active') == before
        assert service.storage.acquisitions() == []
    finally:
        service.close()


@pytest.mark.parametrize(('scenario', 'code'), [
    ('unbound', 'adept_identity_unproven'),
    ('borrower', 'adept_borrower_binding_required'),
    ('foreign_ledger', 'adept_loan_identity_conflict'),
    ('foreign_entitlement', 'adept_loan_identity_conflict'),
])
def test_refused_context_preserves_public_outcome_and_never_falls_back_to_ui(tmp_path, monkeypatch, scenario, code):
    service, loan = _setup(tmp_path, bound=scenario != 'unbound', linked=scenario == 'borrower')
    try:
        if scenario == 'foreign_ledger':
            service.storage.acquisition_put({'acquisition_id': 'prior', 'provider_id': 'paris',
                'credential_id': 'source-b', 'account_id': 'primary', 'identity_id': 'alex',
                'item_id': loan.record_id, 'status': 'complete', 'strategy': 'loan_auto',
                'metadata': {'provider_loan_id': loan.id}})
        elif scenario == 'foreign_entitlement':
            service._persist_active_loans('alex', [replace(loan, source_credential='source-b', identity_id='alex')])
        before_rights = service.storage.entitlements(provider_id='paris', state='active')
        before_ledger = service.storage.acquisitions()
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal['status'] == 'attention_required'
        assert result.refusal['code'] == code and result.refusal['retry'] == 'explicit_only'
        # Exercise the real caller bridge too: it returns the historical dict,
        # not the private context and not a permission to reach later stages.
        assert service._acquire_active_loan_automatically_guarded(loan) == result.refusal
        assert service.current_identity.identity_id == 'alex'
        assert service.storage.entitlements(provider_id='paris', state='active') == before_rights
        assert service.storage.acquisitions() == before_ledger
    finally:
        service.close()
