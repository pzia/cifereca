"""#3252/#3122: mutable BnF borrower scope must be stable through the HTML read.

Synthetic client only; never access real accounts, SSO, ACSM or books.
"""
from dataclasses import replace
import pytest

from cifereca.plugin_api import ProviderConfigurationError
from cifereca_bnf_pret import BnfProviderSession


HTML = (
    '<section class="active-loans"><div class="js-loan">'
    '<a href="/resources/book-synthetic">Synthetic Book</a>'
    '<a href="/loans/loan-synthetic">Private loan action</a>'
    '</div></section>'
)


class Client:
    def __init__(self):
        self.calls = []

    def login(self, username, password):
        self.calls.append("login")
        return HTML

    def activity(self):
        self.calls.append("activity")
        return HTML

    def clear(self):
        self.calls.append("clear")

    def close(self):
        self.calls.append("close")


def fixture():
    client = Client()
    session = BnfProviderSession(
        client=client, credential_id="synthetic-credential",
        label="Synthetic BnF",
        credentials={"username": "synthetic", "password": "synthetic"},
        options={},
    )
    (account,) = session.connect()
    account.identity_id = "synthetic-identity"
    return session, account, client


@pytest.mark.parametrize("change", [
    "identity_changed", "credential_changed", "account_replaced_same_values",
    "account_removed",
])
def test_mutated_borrower_during_activity_rejected_before_typed_publication(change):
    session, account, client = fixture()
    original = session.loans

    def mid_read(borrower):
        rows = original(borrower)
        if change == "identity_changed":
            account.identity_id = "other-identity"
        elif change == "credential_changed":
            account.credential_id = "other-credential"
        elif change == "account_replaced_same_values":
            session._accounts = [replace(account)]
        else:
            session._accounts = []
        return rows

    session.loans = mid_read
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(account)
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


def test_stable_borrower_keeps_partial_and_no_hidden_download():
    session, account, client = fixture()
    try:
        result = session.loan_inventory_snapshot(account)
        assert result.scope.identity_id == "synthetic-identity"
        assert result.status == "partial"
        assert len(result.loans) == 1
        assert result.branches[0].completeness == "partial"
        assert client.calls == ["login", "activity"]
    finally:
        session.close()
