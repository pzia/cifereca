"""A failed Paris reconnect revokes the previous account/session authority.

Synthetic client only: no supplier HTTP, borrow, token GET or ADEPT.
"""
import pytest

from cifereca.plugin_api import ProviderConfigurationError
from tests.test_p1_3252_loan_snapshot_connect_close_serialization import fixture


@pytest.mark.parametrize("failure", ["login", "summary", "disabled", "credentials"])
def test_failed_reconnect_cannot_keep_old_loans_authorized(monkeypatch, failure):
    session, _, client = fixture()
    (old_account,) = session.connect()
    old_account.identity_id = "synthetic-identity"
    session._borrow_claim_attempts.add("previously-spent-operation")
    try:
        if failure == "login":
            def denied(*args):
                raise RuntimeError("synthetic login failure")
            monkeypatch.setattr(client, "login", denied)
        elif failure == "summary":
            def unavailable(*args):
                raise RuntimeError("synthetic summary unavailable")
            monkeypatch.setattr(client, "summary", unavailable)
        elif failure == "disabled":
            monkeypatch.setattr(client, "summary", lambda *args: {"IsDisabled": True})
        else:
            session.credentials = {"username": "", "password": ""}

        with pytest.raises((ProviderConfigurationError, RuntimeError)):
            session.connect()
        assert session.connected is False
        assert session.accounts == []
        before = list(client.calls)
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(old_account)
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loans(old_account)
        assert client.calls == before  # no stale private ListLoans read
        assert session._borrow_claim_attempts == {"previously-spent-operation"}
    finally:
        session.close()


def test_previous_account_is_revoked_before_login_and_success_binds_new_instance(monkeypatch):
    session, old_account, client = fixture()
    login = client.login
    def observe_revocation(*args):
        assert session.connected is False
        assert session.accounts == []
        with pytest.raises(ProviderConfigurationError):
            session.loan_inventory_snapshot(old_account)
        return login(*args)
    monkeypatch.setattr(client, "login", observe_revocation)
    try:
        (new_account,) = session.connect()
        assert session.connected is True
        assert new_account is not old_account
        assert session.accounts == [new_account]
        assert not any(call[0] == "loans" for call in client.calls)
    finally:
        session.close()


def test_failed_replacement_credentials_cannot_reactivate_previous_account(monkeypatch):
    session, old_account, client = fixture()
    login = client.login
    def denied(*args):
        raise RuntimeError("synthetic refusal of replacement credentials")
    monkeypatch.setattr(client, "login", denied)
    try:
        with pytest.raises(RuntimeError):
            session.connect_with_credentials("replacement", "synthetic")
        assert session.connected is False and session.accounts == []
        monkeypatch.setattr(client, "login", login)
        (new_account,) = session.connect_with_credentials("working", "synthetic")
        assert session.connected is True
        assert new_account is not old_account
    finally:
        session.close()
