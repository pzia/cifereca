"""BnF's deliberately non-exhaustive loan refresh cannot mint sync success."""
from types import SimpleNamespace
import pytest
from cifereca.core import sync_coordination
from cifereca.models import Account, Loan
from tests.test_sync_state_contract import _service


@pytest.mark.parametrize('errors', [[{'code':'inventory_not_exhaustive'}], None, {'private':'SECRET'}])
def test_bnf_manual_partial_persists_positive_without_false_success(tmp_path, monkeypatch, errors):
    service = _service(tmp_path)
    account = Account(provider_id='bnf-pret', credential_id='bnf-reader',
                      unique_id='bnf-reader', identity_id='default')
    rows = [Loan(id='synthetic-loan', provider='BNF_PRET', medium='digital',
                 record_id='synthetic-book', title='Synthetic',
                 source_provider='bnf-pret', source_credential='bnf-reader',
                 source_account_id='bnf-reader', identity_id='default')]
    session = SimpleNamespace(provider_id='bnf-pret', credential_id='bnf-reader',
                              connected=True, capabilities=SimpleNamespace(loans=True,personal_library=False),
                              last_loan_errors=errors, loans=lambda _:list(rows), close=lambda:None)
    service.accounts = [account]
    service._sessions[service._session_key('bnf-pret','bnf-reader')] = session
    monkeypatch.setattr(sync_coordination,'acquisition_lock_file',lambda:tmp_path/'writer.lock')
    try:
        result = service.synchronize_account_source(account)
        assert result['status']=='partial' and result['loans']==1
        assert service.storage.entitlements()[0]['state']=='active'
        assert service.storage.latest_sync_run('default') is None
        rows.clear()
        result = service.synchronize_account_source(account)
        assert result['status']=='partial' and result['loans']==0
        assert service.storage.entitlements()[0]['state']=='active'
        assert service.storage.acquisitions()==[]
        assert service.storage.placements()==[]
        assert 'SECRET' not in service.diagnostics.text('test')
    finally:
        service.close()
