"""P0 #5975: retired ADEPT return receipts are exact, read-only source views.

No provider calls, new return claim, ADEPT inventory or credential guessing.
Tests use the real LibraryService facade with inert synthetic callbacks.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.core.return_adept_orchestrator import adept_return_key
from cifereca.models import Account, Loan
from cifereca.services import LibraryService


class Hostile:
    def __str__(self):
        raise AssertionError("never stringify private provider objects")

    def __bool__(self):
        raise AssertionError("never truth-test private provider objects")

    def __eq__(self, _other):
        raise AssertionError("never compare private provider objects")


class HostileString(str):
    def __str__(self):
        raise AssertionError("never stringify subclass")

    def casefold(self):
        raise AssertionError("never casefold subclass")


def _loan(**updates):
    row = Loan(
        id="loan-a", provider="paris", medium="digital",
        record_id="edition-a", title="Synthetic",
        source_provider="paris", source_credential="credential-a",
        source_account_id="borrower-a",
    )
    return replace(row, **updates)


def _account(**updates):
    values = dict(
        provider_id="paris", credential_id="credential-a",
        unique_id="borrower-a", identity_id="identity-a",
    )
    values.update(updates)
    return Account(**values)


def _reader(loan, account, *, on_preflight=None, on_read=None):
    service = LibraryService.__new__(LibraryService)
    calls = []

    def identity(request):
        calls.append(("preflight", request is loan))
        if on_preflight is not None:
            on_preflight()
        return account, None, None

    def read(key):
        calls.append(("read", key))
        if on_read is not None:
            on_read()
        return {"status": "request_sent", "verification": "not_checked"}

    service._return_loan_identity = identity
    service.storage = SimpleNamespace(loan_return_state=read)
    return service, calls


def _failed():
    return {
        "status": "failed", "scope": "adobe_first_paris",
        "code": "loan_return_borrower_unproven", "retry": "none",
    }


def test_exact_historical_receipt_is_public_and_does_not_retry():
    loan = _loan()
    service, calls = _reader(loan, _account())
    response = service.verify_loan_return(loan)
    assert response["status"] == "request_sent"
    assert response["retry"] == "none"
    assert calls == [
        ("preflight", True),
        ("read", adept_return_key(
            "paris", "credential-a", "borrower-a", "loan-a",
        )),
    ]


@pytest.mark.parametrize(("field", "value"), [
    ("source_provider", Hostile()),
    ("source_provider", HostileString("paris")),
    ("source_credential", False),
    ("source_credential", HostileString("credential-a")),
    ("source_account_id", None),
    ("source_account_id", Hostile()),
    ("id", 0),
    ("id", Hostile()),
    ("record_id", Hostile()),
    ("medium", Hostile()),
])
def test_malformed_source_refused_before_any_account_or_receipt_lookup(field, value):
    loan = _loan(**{field: value})
    service, calls = _reader(loan, _account())
    assert service.verify_loan_return(loan) == _failed()
    assert calls == []


@pytest.mark.parametrize(("field", "new_value"), [
    ("source_provider", "other"),
    ("source_credential", "credential-b"),
    ("source_account_id", "borrower-b"),
    ("id", "loan-b"),
    ("record_id", "edition-b"),
    ("medium", "physical"),
    ("id", Hostile()),
])
def test_preflight_source_drift_never_reaches_storage(field, new_value):
    loan = _loan()
    service, calls = _reader(
        loan, _account(),
        on_preflight=lambda: setattr(loan, field, new_value),
    )
    assert service.verify_loan_return(loan) == _failed()
    assert calls == [("preflight", True)]


@pytest.mark.parametrize(("loan_changes", "field", "replacement"), [
    ({}, "source_provider", "PARIS"),
    ({"source_provider": "PARIS"}, "source_provider", "paris"),
    ({"source_account_id": ""}, "source_account_id", "primary"),
    ({"source_account_id": "primary"}, "source_account_id", ""),
    ({}, "source_credential", "credential-b"),
    ({}, "source_account_id", "borrower-b"),
    ({}, "id", "loan-b"),
    ({}, "record_id", "edition-b"),
    ({}, "medium", "physical"),
    ({}, "source_provider", Hostile()),
])
def test_storage_source_drift_drops_receipt_even_if_hash_collides(
    loan_changes, field, replacement,
):
    loan = _loan(**loan_changes)
    account = _account(
        provider_id=loan.source_provider,
        unique_id=loan.source_account_id if loan.source_account_id else "",
    )
    service, calls = _reader(
        loan, account, on_read=lambda: setattr(loan, field, replacement),
    )
    assert service.verify_loan_return(loan) == _failed()
    assert len(calls) == 2 and calls[0] == ("preflight", True)
    assert calls[1][1] == adept_return_key(
        loan_changes.get("source_provider", "paris"),
        "credential-a",
        "primary" if loan_changes.get("source_account_id") in ("", "primary")
        else "borrower-a",
        "loan-a",
    )


@pytest.mark.parametrize(("field", "replacement"), [
    ("unique_id", "borrower-b"),
    ("credential_id", "credential-b"),
    ("provider_id", "PARIS"),
    ("unique_id", Hostile()),
])
def test_storage_account_drift_drops_receipt(field, replacement):
    loan, account = _loan(), _account()
    service, calls = _reader(
        loan, account, on_read=lambda: setattr(account, field, replacement),
    )
    assert service.verify_loan_return(loan) == _failed()
    assert len(calls) == 2


@pytest.mark.parametrize(("field", "replacement"), [
    ("unique_id", Hostile()),
    ("unique_id", False),
    ("unique_id", HostileString("borrower-a")),
    ("credential_id", Hostile()),
    ("provider_id", HostileString("paris")),
])
def test_foreign_account_field_never_selects_receipt(field, replacement):
    loan = _loan()
    account = _account()
    setattr(account, field, replacement)
    service, calls = _reader(loan, account)
    assert service.verify_loan_return(loan) == _failed()
    assert calls == [("preflight", True)]


def test_frozen_empty_primary_alias_keeps_readable_historical_receipt():
    loan = _loan(source_account_id="")
    service, calls = _reader(loan, _account(unique_id=""))
    assert service.verify_loan_return(loan)["status"] == "request_sent"
    assert calls[1] == (
        "read", adept_return_key(
            "paris", "credential-a", "primary", "loan-a",
        ),
    )


def test_foreign_provider_without_its_exact_borrower_cannot_select_receipt():
    loan = _loan(source_provider="synthetic")
    service, calls = _reader(loan, _account())
    assert service.verify_loan_return(loan) == {
        "status": "failed", "scope": "adobe_first_paris",
        "code": "loan_return_borrower_mismatch", "retry": "none",
    }
    # A branded shortcut must not mask the absent exact borrower evidence.
    assert calls == [("preflight", True)]
