"""#3252: BnF active loans -> positive-only rights-observation contract v1.

Synthetic HTML only. No SSO network, download/token/ACSM, SQLite or Core write.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderConfigurationError, ProviderCapabilityError
from cifereca_bnf_pret import BnfProviderSession


def activity(*pairs):
    rows = "".join(
        '<div class="js-loan">'
        f'<a href="/resources/{edition}">Synthetic book</a>'
        f'<a href="/loans/{right}">Synthetic loan</a>'
        '</div>'
        for edition, right in pairs
    )
    return '<section class="active-loans">' + rows + '</section>'


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


class CoreOwnerBroker:
    def __init__(self, identities=("identity-A",)):
        self.identities = identities
        self.calls = 0

    def accounts(self):
        self.calls += 1
        return [
            NS(provider_id="bnf-pret", account_id="credential-A",
               identity_id=identity)
            for identity in self.identities
        ]


def fixture(html):
    client = Client(html)
    broker = CoreOwnerBroker()
    session = BnfProviderSession(
        client=client, credential_id="credential-A",
        label="Synthetic BnF", credentials={
            "username": "synthetic", "password": "synthetic",
        }, options={}, identity_broker=broker,
    )
    (account,) = session.connect()
    account.identity_id = "identity-A"
    return session, account, client


@pytest.mark.parametrize("pairs", [
    (), (("edition-1", "loan-1"),),
    (("edition-1", "loan-1"), ("edition-2", "loan-2")),
])
def test_bnf_positive_loan_rights_0_1_n_without_implicit_acquisition(pairs):
    session, account, client = fixture(activity(*pairs))
    try:
        result = session.rights_observation_snapshot(account)
        assert client.calls == ["login", "activity"]
        assert (result.scope.identity_id, result.scope.provider_id,
                result.scope.credential_id, result.scope.account_id) == (
                    "identity-A", "bnf-pret", "credential-A", "credential-A",
                )
        assert result.branch == "bnf_html"
        assert result.status == result.coverage == "partial"
        assert result.pagination_complete is False
        assert not result.claims_complete_branch
        assert not result.authorizes_mutation
        assert not result.authorizes_negative_reconciliation
        assert [(r.edition_id, r.right_id) for r in result.rights] == list(pairs)
        for edition_id, right_id in pairs:
            right = result.exact(edition_id, right_id, "loan")
            assert right is not None
            assert right.provider_attested and right.state == "active"
            assert right.kind == "loan"
            assert right.evidence_ref == "bnf_html.active_loans"
            assert right.delivery == "unknown"
            assert right.delivery_offer_id == ""
            assert not right.authorizes_mutation
            assert result.exact(edition_id, right_id, "purchase") is None
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    "identity_changed", "credential_changed", "account_replaced",
])
def test_bnf_changed_borrower_during_activity_cannot_publish_rights(change):
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    original = session.loans

    def changed_during_read(borrower):
        rows = original(borrower)
        if change == "identity_changed":
            account.identity_id = "identity-B"
        elif change == "credential_changed":
            account.credential_id = "credential-B"
        else:
            session._accounts = [replace(account)]
        return rows

    session.loans = changed_during_read
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.rights_observation_snapshot(account)
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


def test_bnf_other_account_is_rejected_without_activity_get():
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.rights_observation_snapshot(replace(account))
        assert client.calls == ["login"]
    finally:
        session.close()


@pytest.mark.parametrize("owners", [
    (), ("identity-B",), ("identity-A", "identity-B"),
    ("identity-A", "identity-A"),
])
def test_bnf_owner_missing_foreign_or_ambiguous_is_refused_before_activity(owners):
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    session.identity_broker.identities = owners
    try:
        with pytest.raises(ProviderCapabilityError, match="bnf_rights_owner_unverified"):
            session.rights_observation_snapshot(account)
        assert client.calls == ["login"]  # no private activity GET
    finally:
        session.close()


def test_bnf_rights_need_broker_but_legacy_loan_inventory_stays_supported():
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    session.identity_broker = None
    try:
        with pytest.raises(ProviderCapabilityError, match="bnf_rights_owner_unverified"):
            session.rights_observation_snapshot(account)
        assert client.calls == ["login"]
        inventory = session.loan_inventory_snapshot(account)
        assert len(inventory.loans) == 1
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


@pytest.mark.parametrize("race", [
    "identity_reassigned", "broker_swapped_same_owner", "owner_duplicated",
])
def test_bnf_owner_rebind_during_activity_cannot_publish_rights(race):
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    broker = session.identity_broker
    original = client.activity

    def during_activity():
        html = original()
        if race == "identity_reassigned":
            broker.identities = ("identity-B",)
        elif race == "broker_swapped_same_owner":
            session.identity_broker = CoreOwnerBroker(("identity-A",))
        else:
            broker.identities = ("identity-A", "identity-A")
        return html

    client.activity = during_activity
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.rights_observation_snapshot(account)
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


def test_bnf_pre_read_identity_rebind_is_rejected_without_private_activity():
    session, account, client = fixture(activity(("edition-1", "loan-1")))
    account.identity_id = "identity-B"  # Core still owns identity-A
    try:
        with pytest.raises(ProviderCapabilityError, match="bnf_rights_owner_unverified"):
            session.rights_observation_snapshot(account)
        assert client.calls == ["login"]
    finally:
        session.close()
