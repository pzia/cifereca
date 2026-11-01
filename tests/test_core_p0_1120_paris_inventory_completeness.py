"""P0 #1120: no Paris return can be confirmed from an ambiguous inventory.

All borrower/account/Adobe/provider data are offline fakes. No real
ReturnDigitalLoan, ADEPT mutation, GET, or file deletion is performed.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

import json

import pytest

from tests.test_core_0982_loan_return_reconciliation import (
    _library, _loan, Session,
)
from tests.test_core_0984_adept_first_paris_reconcile import setup as adept_setup


PRIVATE = "https://private.invalid/pnb?auth=DO_NOT_EXPOSE"


class HostileUpstream:
    def __str__(self):
        raise AssertionError("Must not stringify the upstream name")

    def __repr__(self):
        raise AssertionError("Must not repr the upstream name")

    def __eq__(self, _other):
        raise AssertionError("Must not compare hostile upstream objects")


@pytest.mark.parametrize("errors", [
    None,
    [{"upstream": "DILICOM", "error": PRIVATE}],
    [{"upstream": "UNKNOWN", "error": PRIVATE}],
    [{"upstream": HostileUpstream(), "error": PRIVATE}],
    [{"error": PRIVATE}],
    ["SYRACUSE"],
    {"upstream": "SYRACUSE", "error": PRIVATE},
    [{"upstream": "SYRACUSE"}, {"upstream": "DILICOM"}],
])
def test_pnb_return_verifier_requires_complete_digital_upstream(
    tmp_path, errors,
):
    session = Session([])
    session.last_loan_errors = errors
    service = _library(tmp_path, session=session)
    loan = _loan()
    try:
        before = service.loan_return_state(loan)
        first = service.return_digital(loan)
        assert first == {
            "status": "blocked", "code": "loan_return_removed", "retry": "none",
        }
        assert session.posts == [] and session.checks == []
        assert service.loan_return_state(loan) == before
        assert PRIVATE not in json.dumps(first)
        # A retired return cannot even start a verification-retry cycle.
    finally:
        service.close()


@pytest.mark.parametrize("errors", [
    None, [{"upstream": "DILICOM"}], [{"upstream": "UNKNOWN"}],
    [{"upstream": HostileUpstream()}], [{"error": PRIVATE}],
    ["SYRACUSE"], {"upstream": "SYRACUSE"},
])
def test_adobe_first_paris_inventory_verification_has_identical_guard(
    tmp_path, errors,
):
    service, loan, session = adept_setup(tmp_path)
    session.last_loan_errors = errors
    try:
        result = service.verify_paris_return_inventory(loan)
        assert result == {
            "status": "verification_unavailable",
            "verification": "unavailable",
        }
        assert session.posts == 0
        assert session.accounts_read == ["linked-123"]
        assert PRIVATE not in json.dumps(result)
    finally:
        service.close()


def test_typed_dilicom_proof_is_independent_from_syracuse_failure(tmp_path):
    session = Session([])
    session.last_loan_errors = [{"upstream": "SYRACUSE", "error": PRIVATE}]
    attest_paris_dilicom_complete(session, credential_id="paris-a")
    service = _library(tmp_path, session=session)
    loan = _loan()
    try:
        result = service.return_digital(loan)
        assert result == {
            "status": "blocked", "code": "loan_return_removed", "retry": "none",
        }
        assert session.posts == [] and session.checks == []
        assert PRIVATE not in json.dumps(result)
    finally:
        service.close()
    second_root = tmp_path / "other"
    second_root.mkdir()
    service2, loan2, paris2 = adept_setup(second_root)
    paris2.last_loan_errors = [{"upstream": "SYRACUSE", "error": PRIVATE}]
    attest_paris_dilicom_complete(paris2, credential_id="alice-paris")
    try:
        assert service2.verify_paris_return_inventory(loan2) == {
            "status": "provider_return_confirmed",
            "verification": "provider_absent",
        }
        assert paris2.posts == 0
    finally:
        service2.close()
