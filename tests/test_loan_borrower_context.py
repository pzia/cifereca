"""Account lookup compatibility must not weaken the strict return preflight."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.plugin_api import AttentionRequired
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService
from cifereca.core.loan_return_guard import return_key


def account(uid='', *, credential='owned', provider='paris', primary=False):
    return NS(unique_id=uid,credential_id=credential,provider_id=provider,is_primary=primary)


def loan(borrower='linked', *, identifier='loan-one'):
    return NS(id=identifier,source_provider='paris',source_credential='owned',source_account_id=borrower)


@pytest.fixture
def service():
    service=LibraryService.__new__(LibraryService)
    service.accounts=[account('',primary=True),account('linked'),account('linked',credential='other')]
    service.current_account=service.accounts[2]
    service._sessions={}
    return service


@pytest.mark.parametrize('borrower,index',[('primary',0),('',0),('linked',1)])
def test_explicit_borrower_ignores_unrelated_selected_account(service,borrower,index):
    assert service._account_for_loan(loan(borrower)) is service.accounts[index]


def test_unknown_explicit_borrower_cannot_fall_back_to_primary(service):
    with pytest.raises(ProviderConfigurationError): service._account_for_loan(loan('missing'))


def test_legacy_cache_fallback_only_considers_exact_provider_and_credential(service):
    cached=loan(None)
    assert service._account_for_loan(cached) is service.accounts[0]
    service.current_account=service.accounts[1]
    assert service._account_for_loan(cached) is service.accounts[1]
    service.accounts=[service.accounts[1],service.accounts[2]]
    service.current_account=service.accounts[1]
    assert service._account_for_loan(cached) is service.accounts[0]
    service.accounts=[]
    with pytest.raises(ProviderConfigurationError): service._account_for_loan(cached)


@pytest.mark.parametrize('borrower,identifier',[(None,'loan-one'),('linked','')])
def test_strict_return_rejects_missing_provenance_before_account_resolution(service,borrower,identifier):
    service._account_for_loan=Mock()
    with pytest.raises(AttentionRequired) as caught:
        service._return_loan_identity(loan(borrower,identifier=identifier))
    assert caught.value.code=='loan_return_borrower_unproven'
    service._account_for_loan.assert_not_called()


@pytest.mark.parametrize('overrides',[{'unique_id':'different'},
                                    {'credential_id':'other'}, {'provider_id':'kobo'}])
def test_strict_return_rejects_mismatched_resolved_account_before_session_lookup(service,overrides):
    resolved=account('linked'); resolved.__dict__.update(overrides)
    service._account_for_loan=Mock(return_value=resolved)
    service._session_key=Mock()
    with pytest.raises(AttentionRequired) as caught:
        service._return_loan_identity(loan())
    assert caught.value.code=='loan_return_borrower_mismatch'
    service._session_key.assert_not_called()


@pytest.mark.parametrize('connected',[None,False])
def test_strict_return_requires_the_exact_connected_session(service,connected):
    unrelated=NS(connected=True)
    service._sessions[service._session_key('paris','other')]=unrelated
    if connected is not None:
        service._sessions[service._session_key('paris','owned')]=NS(connected=connected)
    with pytest.raises(AttentionRequired) as caught:
        service._return_loan_identity(loan())
    assert caught.value.code=='loan_return_source_unavailable'


@pytest.mark.parametrize('borrower,index,expected',[('primary',0,'primary'),('',0,'primary'),('linked',1,'linked')])
def test_strict_return_preserves_account_session_and_provenance_key(service,borrower,index,expected):
    session=NS(connected=True)
    service._sessions[service._session_key('paris','owned')]=session
    resolved, returned, key=service._return_loan_identity(loan(borrower))
    assert resolved is service.accounts[index] and returned is session
    assert key==return_key('paris','owned',expected,'loan-one')
    assert key!=return_key('paris','other',expected,'loan-one')


def test_explicit_borrower_never_reads_legacy_current_account(service):
    del service.current_account
    assert service._account_for_loan(loan('linked')) is service.accounts[1]


def test_missing_borrower_does_not_read_session_mapping(service):
    del service._sessions
    with pytest.raises(AttentionRequired) as caught:
        service._return_loan_identity(loan(None))
    assert caught.value.code=='loan_return_borrower_unproven'
