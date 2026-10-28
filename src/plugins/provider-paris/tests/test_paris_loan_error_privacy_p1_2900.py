"""#2900: the Paris loan branch must never emit upstream exception text.

This tests provider-owned diagnostics at emission, not merely Core redaction.
No real Paris connection or loan is performed.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import Account, ProviderConfigurationError
from cifereca_paris import ParisProviderSession


PRIVATE = "PRIVATE_READER_OPAQUE_77384429"


class Config:
    def get(self, _key, default=None):
        return default


class Diagnostics:
    def __init__(self):
        self.events = []

    def add(self, *args, **kwargs):
        self.events.append((args, kwargs))


def _session():
    diagnostics = Diagnostics()
    session = ParisProviderSession(
        credential_id="reader-PRIVATE", label="Paris",
        credentials={"username": "account-PRIVATE", "password": "PRIVATE_PASS"},
        options={}, config=Config(), diagnostics=diagnostics,
    )
    account = Account(
        display_name="PRIVATE_ACCOUNT_NAME", unique_id="PRIVATE_BARCODE",
        provider_id="paris", credential_id="reader-PRIVATE",
    )
    # A legacy private loan GET is valid only for a connected, registered
    # borrower; transport-error fixtures should exercise that same boundary.
    session.connected = True
    session._accounts = [account]
    return session, account, diagnostics


def test_partial_dilicom_error_is_closed_without_losing_syracuse_rows(monkeypatch):
    session, account, diagnostics = _session()
    def loans(upstream, _uid):
        if upstream == "DILICOM":
            raise RuntimeError(PRIVATE + " /loan/signed-path")
        return []
    monkeypatch.setattr(session.client, "loans", loans)
    try:
        assert session.loans(account) == []
        assert session.last_loan_errors == [{
            "upstream": "DILICOM", "code": "paris_loans_upstream_failed",
        }]
        assert diagnostics.events
        serialized = repr((session.last_loan_errors, diagnostics.events))
        assert PRIVATE not in serialized
        assert "PRIVATE_BARCODE" not in serialized
        assert "reader-PRIVATE" not in serialized
    finally:
        session.close()


def test_two_failures_return_only_public_error_without_chained_secret(monkeypatch):
    session, account, diagnostics = _session()
    def loans(_upstream, _uid):
        raise RuntimeError("OPAQUE_PRIVATE_NONURL_ACCOUNT_DATA")
    monkeypatch.setattr(session.client, "loans", loans)
    try:
        with pytest.raises(ProviderConfigurationError) as ctx:
            session.loans(account)
        assert "DILICOM" in str(ctx.value)
        assert "SYRACUSE" in str(ctx.value)
        assert "OPAQUE_PRIVATE" not in str(ctx.value)
        assert ctx.value.__cause__ is None
        assert ctx.value.__suppress_context__ is True
        assert len(session.last_loan_errors) == 2
        assert {x["upstream"] for x in session.last_loan_errors} == {
            "DILICOM", "SYRACUSE",
        }
        assert "OPAQUE_PRIVATE" not in repr(diagnostics.events)
    finally:
        session.close()


def test_later_success_resets_partial_error_state(monkeypatch):
    session, account, diagnostics = _session()
    responses = {"DILICOM": RuntimeError(PRIVATE), "SYRACUSE": []}
    def loans(upstream, _uid):
        response = responses[upstream]
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(session.client, "loans", loans)
    try:
        assert session.loans(account) == []
        assert session.last_loan_errors
        responses["DILICOM"] = []
        assert session.loans(account) == []
        assert session.last_loan_errors == []
    finally:
        session.close()
