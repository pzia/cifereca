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
    original = session._read_live_loans_locked

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

    session._read_live_loans_locked = mid_read
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


def test_typed_bnf_loan_inventory_accepts_exact_core_account_copy():
    session, account, client = fixture()
    copy = replace(account)
    try:
        snapshot = session.loan_inventory_snapshot(copy)
        assert snapshot.scope.identity_id == "synthetic-identity"
        assert snapshot.scope.credential_id == "synthetic-credential"
        assert len(snapshot.loans) == 1
        assert snapshot.loans[0].identity_id == copy.identity_id
        assert client.calls == ["login", "activity"]
        # Keep v1 instance-only behavior to avoid broadening its authority.
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(copy)
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


@pytest.mark.parametrize("field,foreign", [
    ("identity_id", "foreign-identity"),
    ("credential_id", "foreign-credential"),
    ("provider_id", "paris"),
    ("unique_id", "foreign-account"),
])
def test_typed_bnf_inventory_foreign_core_copy_refused_before_get(field, foreign):
    session, account, client = fixture()
    copy = replace(account, **{field: foreign})
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(copy)
        assert client.calls == ["login"]
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    "live_replaced_same_values", "caller_rebound", "bound_rebound", "live_removed",
])
def test_typed_bnf_inventory_copy_refuses_inflight_session_rebind(change):
    session, account, client = fixture()
    copy = replace(account)
    original = session._read_live_loans_locked

    def mutate_after_read(borrower):
        rows = original(borrower)
        if change == "live_replaced_same_values":
            session._accounts = [replace(account)]
        elif change == "caller_rebound":
            copy.identity_id = "other-identity"
        elif change == "bound_rebound":
            account.identity_id = "other-identity"
        else:
            session._accounts = []
        return rows

    session._read_live_loans_locked = mutate_after_read
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(copy)
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


def test_public_typed_inventory_reader_accepts_bnf_core_copy_without_legacy_retry():
    from cifereca.plugin_api import read_loan_inventory

    session, account, client = fixture()
    copy = replace(account)
    try:
        report = read_loan_inventory(session, copy)
        assert report.status == "partial"
        assert report.scope.identity_id == "synthetic-identity"
        assert len(report.loans) == 1
        assert report.loans[0].source_credential == "synthetic-credential"
        assert report.reason_code == "inventory_not_exhaustive"
        # Reader does not fall back to the instance-only legacy loans(copy).
        assert client.calls == ["login", "activity"]
    finally:
        session.close()
