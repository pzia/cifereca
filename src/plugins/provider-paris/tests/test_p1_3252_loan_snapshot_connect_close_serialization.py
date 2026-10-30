"""#3252/#3122: Paris session replacement must not split one inventory snapshot.

Synthetic legacy client only; no real credentials, network or account actions.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from cifereca.plugin_api import Account, Loan, ProviderConfigurationError
from cifereca_paris import ParisProviderSession


class Client:
    def __init__(self):
        self.calls = []

    def loans(self, upstream, uid):
        self.calls.append(("loans", upstream, uid))
        return [
            Loan(
                id="synthetic-" + upstream, provider=upstream,
                medium="digital" if upstream == "DILICOM" else "physical",
                record_id="synthetic", title="Synthetic",
            )
        ]

    def login(self, username, password):
        self.calls.append(("login",))
        return None

    def summary(self, upstream, uid):
        self.calls.append(("summary", upstream))
        return {"DisplayName": "Synthetic Reader"}

    def pairings(self):
        self.calls.append(("pairings",))
        return []

    def close(self):
        self.calls.append(("close",))


def fixture():
    session = ParisProviderSession(
        credential_id="synthetic-card", label="Paris",
        credentials={"username": "synthetic", "password": "synthetic"},
        options={}, config={}, diagnostics=None,
    )
    session.client.close()
    client = Client()
    session.client = client
    session._enrich_digital_loan_metadata = lambda loan: None
    account = Account(
        unique_id="", is_primary=True, provider_id="paris",
        credential_id="synthetic-card", identity_id="synthetic-identity",
    )
    session.connected = True
    session._accounts = [account]
    return session, account, client


@pytest.mark.parametrize("change", ["reconnect", "close"])
def test_connect_or_close_cannot_interleave_legacy_read_and_typed_snapshot(change):
    session, account, client = fixture()
    got_loans, release_read, change_started, change_done = (
        Event(), Event(), Event(), Event()
    )
    old = session._read_loans_for_account

    def pause_after_legacy_read(bound_account):
        rows = old(bound_account)
        got_loans.set()
        if not release_read.wait(10):
            raise AssertionError("Synthetic Paris read blocked unexpectedly")
        return rows

    session._read_loans_for_account = pause_after_legacy_read

    def change_session():
        change_started.set()
        try:
            if change == "reconnect":
                return session.connect()
            session.close()
            return None
        finally:
            change_done.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(session.loan_inventory_snapshot, account)
            assert got_loans.wait(10)
            second = pool.submit(change_session)
            assert change_started.wait(10)
            try:
                assert not change_done.wait(0.15)
            finally:
                release_read.set()
            snapshot = first.result(timeout=10)
            second.result(timeout=10)

        assert snapshot.status == "success"
        assert snapshot.scope.account_id == ""
        assert len(snapshot.loans) == 2
        assert all(b.completeness == "unknown" for b in snapshot.branches)
        assert client.calls[:2] == [
            ("loans", "DILICOM", ""), ("loans", "SYRACUSE", ""),
        ]
        if change == "reconnect":
            assert session.connected
            assert session._accounts[0] is not account
            assert client.calls[2:] == [
                ("login",), ("summary", "SYRACUSE"), ("pairings",),
            ]
        else:
            assert session.connected is False
            assert client.calls[2:] == [("close",)]
            with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
                session.loan_inventory_snapshot(account)
    finally:
        release_read.set()
        session.close()
