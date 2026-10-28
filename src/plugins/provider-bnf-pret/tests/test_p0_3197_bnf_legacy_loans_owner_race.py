"""#3197/#3252: legacy BnF loans cannot survive an in-flight owner rebind.

One fake activity() call; never SSO, loan tokens, ACSM or real network.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import ProviderConfigurationError
from cifereca_bnf_pret import BnfProviderSession


def activity(*pairs):
    return ('<section class="active-loans">'
            + ''.join(
                f'<div class="js-loan"><a href="/resources/{edition}">Book</a>'
                f'<a href="/loans/{right}">Loan</a></div>'
                for edition, right in pairs
            )
            + '</section>')


class Client:
    def __init__(self, html):
        self.html = html
        self.calls = []

    def login(self, username, password):
        self.calls.append("login")
        return activity()

    def activity(self):
        self.calls.append("activity")
        return self.html

    def clear(self):
        self.calls.append("clear")

    def close(self):
        self.calls.append("close")


def fixture(html):
    client = Client(html)
    session = BnfProviderSession(
        client=client, credential_id="credential-A",
        label="Synthetic BnF",
        credentials={"username": "synthetic", "password": "synthetic"},
        options={},
    )
    (account,) = session.connect()
    account.identity_id = "identity-A"
    return session, account, client


@pytest.mark.parametrize("race", [
    "identity_rebound", "account_changed", "credential_rebound",
    "provider_rebound", "account_replaced_same_values",
    "account_removed", "disconnected",
])
def test_bnf_legacy_loans_discard_inflight_ambiguous_account_and_token_state(race):
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    original = client.activity

    def during_activity():
        response = original()
        if race == "identity_rebound":
            account.identity_id = "identity-B"
        elif race == "account_changed":
            account.unique_id = "other-account"
        elif race == "credential_rebound":
            account.credential_id = "other-credential"
        elif race == "provider_rebound":
            account.provider_id = "paris"
        elif race == "account_replaced_same_values":
            session._accounts = [replace(account)]
        elif race == "account_removed":
            session._accounts = []
        else:
            session.connected = False
        return response

    client.activity = during_activity
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(account)
        assert client.calls == ["login", "activity"]
        # A stale token lookup is not justified by this failed legacy read.
        assert session._observed_loans == {}
    finally:
        session.close()


def test_bnf_legacy_loans_refuse_mismatched_active_credential_before_activity():
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    account.credential_id = "other-credential"
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(account)
        assert client.calls == ["login"]
        assert session._observed_loans == {}
    finally:
        session.close()


def test_bnf_legacy_loans_stable_scope_still_delivers_one_positive_loan():
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    try:
        rows = session.loans(account)
        assert [(x.id, x.record_id, x.identity_id) for x in rows] == [
            ("loan-1", "edition-1", "identity-A"),
        ]
        assert session._observed_loans == {"loan-1": "edition-1"}
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


class OwnerBroker:
    def __init__(self, identity="identity-A"):
        self.identity = identity

    def accounts(self):
        from types import SimpleNamespace
        return [SimpleNamespace(
            provider_id="bnf-pret", account_id="credential-A",
            identity_id=self.identity,
        )]


@pytest.mark.parametrize("race", [
    "owner_changed", "broker_swapped_same_owner", "duplicated_owner",
])
def test_bnf_legacy_loans_refuse_inflight_broker_owner_rebind(race):
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    broker = OwnerBroker()
    session.identity_broker = broker
    original = client.activity

    def during_activity():
        response = original()
        if race == "owner_changed":
            broker.identity = "identity-B"
        elif race == "broker_swapped_same_owner":
            session.identity_broker = OwnerBroker("identity-A")
        else:
            broker.accounts = lambda: [
                *OwnerBroker("identity-A").accounts(),
                *OwnerBroker("identity-A").accounts(),
            ]
        return response

    client.activity = during_activity
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(account)
        assert client.calls == ["login", "activity"]
        assert session._observed_loans == {}
    finally:
        session.close()


@pytest.mark.parametrize("owner", ["identity-B", ""])
def test_bnf_legacy_loans_refuse_unlinked_broker_before_private_get(owner):
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    session.identity_broker = OwnerBroker(owner)
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(account)
        assert client.calls == ["login"]
    finally:
        session.close()


def test_bnf_legacy_loans_stable_owner_broker_keeps_one_get():
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    session.identity_broker = OwnerBroker()
    try:
        result = session.loans(account)
        assert len(result) == 1
        assert result[0].identity_id == "identity-A"
        assert client.calls == ["login", "activity"]
    finally:
        session.close()
