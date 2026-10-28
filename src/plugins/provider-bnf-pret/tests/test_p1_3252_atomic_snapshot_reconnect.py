"""#3252/#3122: BnF loan observations must be bound to one connected epoch.

No real SSO, private HTML, token, ACSM, rights mutation or network access.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

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
        label="Synthetic BnF", credentials={
            "username": "synthetic", "password": "synthetic",
        }, options={},
    )
    (account,) = session.connect()
    account.identity_id = "synthetic-identity"
    return session, account, client


def test_reconnect_cannot_replace_account_between_loan_read_and_snapshot():
    session, original_account, client = fixture()
    observed, release, reconnect_started, reconnect_completed = (
        Event(), Event(), Event(), Event()
    )
    legacy = session._read_live_loans_locked

    def hold_after_loan_get(account):
        rows = legacy(account)
        observed.set()
        if not release.wait(10):
            raise AssertionError("Synthetic BnF snapshot timeout")
        return rows

    session._read_live_loans_locked = hold_after_loan_get

    def reconnect():
        reconnect_started.set()
        result = session.connect()
        reconnect_completed.set()
        return result

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(session.loan_inventory_snapshot, original_account)
            assert observed.wait(10)
            second = pool.submit(reconnect)
            assert reconnect_started.wait(10)
            try:
                # A reconnect must not replace the session between the GET
                # and the typed envelope; legacy loans() already had a lock.
                assert not reconnect_completed.wait(0.15)
            finally:
                release.set()
            snapshot = first.result(timeout=10)
            (new_account,) = second.result(timeout=10)

        assert snapshot.status == "partial"
        assert snapshot.scope.account_id == original_account.unique_id
        assert len(snapshot.loans) == 1
        assert snapshot.loans[0].identity_id == "synthetic-identity"
        assert snapshot.branches[0].completeness == "partial"
        assert new_account is not original_account
        assert client.calls == ["login", "activity", "login"]
        # The old account is no longer a source after reconnect.
        from cifereca.plugin_api import ProviderConfigurationError
        import pytest
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.loan_inventory_snapshot(original_account)
        assert client.calls == ["login", "activity", "login"]
    finally:
        release.set()
        session.close()


def test_read_only_snapshot_does_not_call_token_or_fulfillment():
    session, account, client = fixture()
    try:
        snapshot = session.loan_inventory_snapshot(account)
        assert snapshot.status == "partial"
        assert len(snapshot.loans) == 1
        assert client.calls == ["login", "activity"]
        assert snapshot.branches[0].reason_code == "inventory_not_exhaustive"
        assert not snapshot.branch_claims_complete("bnf_html", "digital")
    finally:
        session.close()
