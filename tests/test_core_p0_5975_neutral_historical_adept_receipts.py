"""#5975: historical ADEPT return receipts are source-neutral, inert and private.

A future lender may possess a historical ADEPT checkpoint without becoming
eligible for any return action. Exact source/borrower preflight is mandatory.
"""
from __future__ import annotations

import pytest

from cifereca.core.return_adept_orchestrator import adept_return_key
from tests.test_core_p0_5975_adept_return_receipt_read_lease import (
    _account, _failed, _loan, _reader,
)


@pytest.mark.parametrize("provider", [
    "biblioaccess.la-rochelle", "future-library",
])
def test_exact_nonparis_source_can_read_only_its_own_historical_receipt(provider):
    loan = _loan(source_provider=provider)
    account = _account(provider_id=provider)
    service, calls = _reader(loan, account)
    result = service.verify_loan_return(loan)
    assert result == {
        "status": "request_sent", "scope": "adobe_first_paris",
        "adobe_return_confirmed": False, "provider_return_confirmed": False,
        "paris_verification": "not_checked", "requested_at": None,
        "checked_at": None, "retry": "none",
    }
    assert calls == [
        ("preflight", True),
        ("read", adept_return_key(
            provider, "credential-a", "borrower-a", "loan-a",
        )),
    ]


@pytest.mark.parametrize("mismatch", [
    {"provider_id": "paris"},
    {"credential_id": "other-credential"},
    {"unique_id": "borrower-b"},
])
def test_nonparis_foreign_borrower_does_not_read_other_source_checkpoint(mismatch):
    loan = _loan(source_provider="future-library")
    params = {
        "provider_id": "future-library", "credential_id": "credential-a",
        "unique_id": "borrower-a",
    }
    params.update(mismatch)
    service, calls = _reader(loan, _account(**params))
    assert service.verify_loan_return(loan) == {
        "status": "failed", "scope": "adobe_first_paris",
        "code": "loan_return_borrower_mismatch", "retry": "none",
    }
    assert calls == [("preflight", True)]


@pytest.mark.parametrize("field,value", [
    ("source_provider", "other-library"),
    ("source_credential", "other-credential"),
    ("source_account_id", "other-borrower"),
    ("id", "other-loan"),
    ("record_id", "other-edition"),
    ("medium", "physical"),
])
def test_nonparis_rebind_during_checkpoint_read_discards_positive(field, value):
    loan = _loan(source_provider="future-library")
    service, calls = _reader(
        loan, _account(provider_id="future-library"),
        on_read=lambda: setattr(loan, field, value),
    )
    assert service.verify_loan_return(loan) == _failed()
    assert calls[0] == ("preflight", True)
    assert calls[1][0] == "read"


def test_nonparis_missing_checkpoint_remains_not_requested_without_mutations():
    loan = _loan(source_provider="future-library")
    service, calls = _reader(
        loan, _account(provider_id="future-library"),
    )
    service.storage.loan_return_state = lambda key: calls.append(
        ("read", key)
    ) or None
    assert service.verify_loan_return(loan) == {
        "status": "not_requested", "scope": "adobe_first_paris",
    }
    assert calls == [
        ("preflight", True),
        ("read", adept_return_key(
            "future-library", "credential-a", "borrower-a", "loan-a",
        )),
    ]


def test_nonparis_physical_loan_never_reads_adept_checkpoint():
    loan = _loan(source_provider="future-library", medium="physical")
    service, calls = _reader(loan, _account(provider_id="future-library"))
    assert service.verify_loan_return(loan) == {
        "status": "not_requested", "scope": "adobe_first_paris",
    }
    assert calls == []


def test_nonparis_rejected_preflight_does_not_fall_back_to_paris_or_ui():
    loan = _loan(source_provider="future-library")
    service, calls = _reader(loan, _account(provider_id="paris"))
    assert service.verify_loan_return(loan)["status"] == "failed"
    assert calls == [("preflight", True)]


@pytest.mark.parametrize("method", [
    "return_loan_prefer_adept", "return_digital",
    "_return_loan_prefer_adept_guarded", "_return_digital_guarded",
])
def test_read_only_nonparis_receipt_never_reopens_retired_return_command(method):
    from cifereca.plugin_api import AttentionRequired

    loan = _loan(source_provider="future-library")
    service, calls = _reader(loan, _account(provider_id="future-library"))
    # Even a readable historical status is not an action authorization.
    assert service.verify_loan_return(loan)["status"] == "request_sent"
    before_calls = tuple(calls)
    with pytest.raises(AttentionRequired) as failure:
        getattr(service, method)(loan)
    assert failure.value.code == "loan_return_removed"
    assert tuple(calls) == before_calls
