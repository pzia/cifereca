"""#3197: a legacy Paris private GET must use one live bound borrower.

Synthetic loans, no real login, account data, acquisition or network request.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import Account, ProviderConfigurationError
from tests.test_p1_3252_rights_observation_snapshot import fixture


@pytest.mark.parametrize("variant", [
    "unconnected", "unregistered", "foreign_account", "foreign_credential",
    "foreign_provider", "duplicated_account", "absent_live_account",
])
def test_paris_legacy_loans_refuse_foreign_or_unregistered_borrower_before_get(variant):
    session, account, client = fixture()
    if variant == "unconnected":
        session.connected = False
    elif variant == "unregistered":
        session._accounts = [replace(account, unique_id="reader-B")]
    elif variant == "foreign_account":
        account = replace(account, unique_id="reader-B")
    elif variant == "foreign_credential":
        account = replace(account, credential_id="other-credential")
    elif variant == "foreign_provider":
        account = replace(account, provider_id="bnf-pret")
    elif variant == "duplicated_account":
        session._accounts.append(replace(account))
    elif variant == "absent_live_account":
        session._accounts = []
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(account)
        assert client.calls == []
    finally:
        session.close()


@pytest.mark.parametrize("mutation", [
    "account_replaced_same_scope", "active_scope_rebound", "account_disappeared",
])
def test_paris_legacy_loans_refuse_live_account_change_before_second_get(mutation):
    session, account, client = fixture()
    requested = []

    def loans(branch, borrower):
        requested.append((branch, borrower))
        if branch == "DILICOM":
            if mutation == "account_replaced_same_scope":
                session._accounts = [replace(account)]
            elif mutation == "active_scope_rebound":
                account.identity_id = "identity-B"
            else:
                session._accounts = []
        return []

    client.loans = loans
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(account)
        assert requested == [("DILICOM", "reader-A")]
    finally:
        session.close()


def test_paris_legacy_loans_allow_exact_registered_core_copy():
    session, account, client = fixture()
    copy = replace(account)
    try:
        assert session.loans(copy) == []
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()


def test_paris_legacy_primary_empty_id_stays_a_legitimate_borrower():
    session, account, client = fixture()
    account.unique_id = ""
    account.is_primary = True
    # Both client calls are still exactly scoped to the empty primary.
    try:
        assert session.loans(account) == []
        assert client.calls == [("DILICOM", ""), ("SYRACUSE", "")]
    finally:
        session.close()
