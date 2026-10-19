"""P0 #1120: no account lookup, session or return POST from malformed loan provenance.

Headless exact Core borrower preflight. All account, session and loan objects
are synthetic; no connected provider, ADEPT, network, SQLite or file mutation.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_accounts import return_loan_identity
from cifereca.core.loan_return_guard import return_key
from cifereca.plugin_api import AttentionRequired


PRIVATE = "https://private.invalid/pnb?auth=PRIVATE_BORROWER_TOKEN"


class Hostile:
    def __str__(self):
        raise AssertionError("Never stringify a malformed borrower")

    def __bool__(self):
        raise AssertionError("Never coerce a malformed borrower")

    def __eq__(self, _other):
        raise AssertionError("Never compare malformed borrower")

    def __repr__(self):
        raise AssertionError("Never repr a malformed borrower")


def _loan(**values):
    return NS(**{
        "id": "loan-123", "source_provider": "paris",
        "source_credential": "credential-123",
        "source_account_id": "linked",
        **values,
    })


def _admission(row, *, account=None, session=None):
    reads = []
    owner = account or NS(
        unique_id="linked", provider_id="paris",
        credential_id="credential-123",
    )
    connected = session or NS(connected=True)

    def resolve(_loan):
        reads.append("resolve")
        return owner

    def source_key(provider, credential):
        reads.append("source_key")
        assert (provider, credential) == ("paris", "credential-123")
        return "exact"

    def session_for(key):
        reads.append("session")
        assert key == "exact"
        return connected

    kwargs = dict(
        resolve_account=resolve, session_key=source_key,
        lookup_session=session_for, attention_error=AttentionRequired,
    )
    return kwargs, reads


@pytest.mark.parametrize("change", [
    {"id": Hostile()}, {"id": 12}, {"id": True}, {"id": ""},
    {"source_provider": Hostile()}, {"source_provider": ["paris"]},
    {"source_credential": Hostile()}, {"source_credential": ""},
    {"source_account_id": Hostile()},
    {"source_account_id": None},
    {"source_account_id": 0},
    {"source_account_id": [PRIVATE]},
])
def test_malformed_loan_source_never_reaches_account_selection(change):
    kwargs, reads = _admission(_loan())
    with pytest.raises(AttentionRequired) as caught:
        return_loan_identity(_loan(**change), **kwargs)
    assert caught.value.code == "loan_return_borrower_unproven"
    assert "PRIVATE_BORROWER_TOKEN" not in str(caught.value)
    assert reads == []


@pytest.mark.parametrize("change", [
    {"unique_id": Hostile()}, {"unique_id": True},
    {"provider_id": Hostile()}, {"provider_id": ["paris"]},
    {"credential_id": Hostile()}, {"credential_id": [PRIVATE]},
    {"unique_id": "other-borrower"},
])
def test_corrupt_resolved_account_never_constructs_return_session(change):
    owner = NS(**{
        "unique_id": "linked", "provider_id": "paris",
        "credential_id": "credential-123", **change,
    })
    kwargs, reads = _admission(_loan(), account=owner)
    with pytest.raises(AttentionRequired) as caught:
        return_loan_identity(_loan(), **kwargs)
    assert caught.value.code == "loan_return_borrower_mismatch"
    assert reads == ["resolve"]


@pytest.mark.parametrize("connected", [None, False, 1, "true", Hostile()])
def test_ambiguous_session_readiness_never_proves_return_source(connected):
    session = NS(connected=connected)
    kwargs, reads = _admission(_loan(), session=session)
    with pytest.raises(AttentionRequired) as caught:
        return_loan_identity(_loan(), **kwargs)
    assert caught.value.code == "loan_return_source_unavailable"
    assert reads == ["resolve", "source_key", "session"]


@pytest.mark.parametrize(("borrower", "account_uid", "expected"), [
    ("linked", "linked", "linked"),
    ("primary", "", "primary"),
    ("", "", "primary"),
])
def test_exact_connected_paris_borrower_key_is_preserved(borrower, account_uid, expected):
    loan = _loan(source_account_id=borrower)
    account = NS(
        unique_id=account_uid, provider_id="paris",
        credential_id="credential-123",
    )
    kwargs, reads = _admission(loan, account=account)
    owner, session, hashed = return_loan_identity(loan, **kwargs)
    assert owner is account and session.connected is True
    assert hashed == return_key("paris", "credential-123", expected, "loan-123")
    assert reads == ["resolve", "source_key", "session"]
