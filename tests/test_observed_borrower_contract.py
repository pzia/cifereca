from dataclasses import replace

import pytest

from cifereca.models import Account, Loan
from cifereca.providers.base import ProviderDescriptor, ProviderCapabilities
from cifereca.plugin_api.loan_scope import loan_matches_account


def subject(provider='synthetic-a', borrower='', alias='root-reader'):
    account = Account(provider_id=provider, credential_id='connection',
                      identity_id='alice', unique_id=borrower, is_primary=True)
    loan = Loan(id='occurrence', provider='upstream', medium='digital',
                record_id='edition', title='Title', identity_id='alice',
                source_provider=provider, source_credential='connection',
                source_account_id=alias)
    descriptor = ProviderDescriptor(provider, 'Synthetic', '', 'ready',
                                    ProviderCapabilities(),
                                    existing_primary_loan_account_alias=alias)
    return loan, account, descriptor


@pytest.mark.parametrize('provider', ['synthetic-a', 'synthetic-b', 'unknown'])
def test_declared_primary_alias_is_provider_neutral(provider):
    loan, account, descriptor = subject(provider)
    assert loan_matches_account(loan, account, descriptor)
    assert loan_matches_account(replace(loan, source_account_id=''), account, descriptor)
    assert not loan_matches_account(loan, replace(account, is_primary=False), descriptor)
    assert not loan_matches_account(loan, replace(account, unique_id='linked-reader'), descriptor)
    assert not loan_matches_account(loan, replace(account, is_primary=1), descriptor)


@pytest.mark.parametrize('field,value', [
    ('identity_id', 'bob'), ('source_provider', 'synthetic-b'),
    ('source_credential', 'other-connection'), ('source_account_id', 'other-reader'),
    ('identity_id', None), ('source_account_id', None),
])
def test_crossed_or_missing_scope_is_closed(field, value):
    loan, account, descriptor = subject()
    assert not loan_matches_account(replace(loan, **{field: value}), account, descriptor)


@pytest.mark.parametrize('alias', ['', 'Bad Alias', 'a' * 65, 0, True, object()])
def test_invalid_alias_does_not_fall_back_to_exact_match(alias):
    loan, account, descriptor = subject(borrower='reader', alias='reader')
    assert not loan_matches_account(loan, account, replace(descriptor, existing_primary_loan_account_alias=alias))


def test_missing_alias_allows_only_exact_account_not_legacy_sentinel():
    loan, account, descriptor = subject()
    descriptor = replace(descriptor, existing_primary_loan_account_alias=None)
    assert not loan_matches_account(loan, account, descriptor)
    assert loan_matches_account(replace(loan, source_account_id=''), account, descriptor)
    # No alias is necessary for an ordinary linked borrower.
    assert loan_matches_account(replace(loan, source_account_id='linked'),
                                replace(account, unique_id='linked', is_primary=False), descriptor)


def test_descriptor_cannot_be_borrowed_from_another_provider():
    loan, account, descriptor = subject()
    assert not loan_matches_account(loan, account, replace(descriptor, id='synthetic-b'))
    assert not loan_matches_account(loan, account, None)


def test_raw_metadata_does_not_declare_alias_or_rebind_identity():
    loan, account, descriptor = subject()
    loan.raw = {'existing_primary_loan_account_alias': 'root-reader', 'identity_id': 'alice'}
    assert not loan_matches_account(loan, account, replace(descriptor, existing_primary_loan_account_alias=None))
    assert not loan_matches_account(replace(loan, identity_id='bob'), account, descriptor)


def test_no_dynamic_provider_or_proxy_code_is_executed():
    class Hostile:
        def __getattribute__(self, name):
            raise AssertionError('unexpected provider call')
    loan, account, descriptor = subject()
    assert not loan_matches_account(Hostile(), account, descriptor)
    assert not loan_matches_account(loan, Hostile(), descriptor)
    assert not loan_matches_account(loan, account, Hostile())
