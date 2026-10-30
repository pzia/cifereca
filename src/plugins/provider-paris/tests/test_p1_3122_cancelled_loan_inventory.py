"""#3122: a cancelled Paris ListLoans read must not become a partial success.

Both branches are synthetic: no network, login, personal data or DRM.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import Account, Cancelled, Loan
from cifereca_paris import ParisProviderSession


class NoopDiagnostics:
    def __init__(self):
        self.events = []

    def add(self, *args, **kwargs):
        self.events.append((args, kwargs))


class FakeClient:
    def __init__(self, cancelled_upstream):
        self.calls = []
        self.cancelled_upstream = cancelled_upstream

    def loans(self, upstream, account_id):
        self.calls.append((upstream, account_id))
        if upstream == self.cancelled_upstream:
            raise Cancelled("task cancelled while reading " + upstream)
        return [
            Loan(
                id="loan-" + upstream, provider=upstream,
                medium="digital" if upstream == "DILICOM" else "physical",
                record_id="synthetic", title="Test",
            )
        ]

    def close(self):
        pass


def session(cancelled_upstream):
    sink = NoopDiagnostics()
    obj = ParisProviderSession(
        credential_id="reader-test", label="Paris", credentials={},
        options={}, config={}, diagnostics=sink,
    )
    obj.client.close()
    obj.client = FakeClient(cancelled_upstream)
    obj._enrich_digital_loan_metadata = lambda row: None
    account = Account(
        unique_id="linked-test", display_name="Test",
        provider_id="paris", credential_id="reader-test",
        identity_id="identity-test",
    )
    obj._accounts = [account]
    obj.connected = True
    return obj, account, sink


@pytest.mark.parametrize("upstream,expected_calls", [
    ("DILICOM", ["DILICOM"]),
    ("SYRACUSE", ["DILICOM", "SYRACUSE"]),
])
@pytest.mark.parametrize("entrypoint", ["loans", "loan_inventory_snapshot"])
def test_cancel_propagates_and_never_generates_inventory_status(
    upstream, expected_calls, entrypoint,
):
    obj, account, sink = session(upstream)
    try:
        with pytest.raises(Cancelled, match="task cancelled"):
            getattr(obj, entrypoint)(account)
        assert obj.client.calls == [(name, account.unique_id) for name in expected_calls]
        assert "last_loan_errors" not in vars(obj)
        assert not any(
            kwargs.get("detail", {}).get("code") == "paris_loans_upstream_failed"
            for _args, kwargs in sink.events
        )
    finally:
        obj.close()


def test_first_failed_branch_then_cancel_still_does_not_publish_partial():
    obj, account, sink = session("SYRACUSE")
    original = obj.client.loans

    def first_fails(upstream, uid):
        if upstream == "DILICOM":
            obj.client.calls.append((upstream, uid))
            raise RuntimeError("synthetic DILICOM outage")
        return original(upstream, uid)

    obj.client.loans = first_fails
    try:
        with pytest.raises(Cancelled):
            obj.loan_inventory_snapshot(account)
        assert obj.client.calls == [
            ("DILICOM", account.unique_id), ("SYRACUSE", account.unique_id),
        ]
        # A preceding real branch outage can be diagnostic, but a subsequent
        # cancellation must not return an authoritative or partial snapshot.
        assert len(sink.events) == 1
        assert sink.events[0][1]["detail"]["upstream"] == "DILICOM"
    finally:
        obj.close()
