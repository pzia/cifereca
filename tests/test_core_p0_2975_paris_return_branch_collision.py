"""P0 #2975: same-ID Paris branch collision is not evidence of return.

PNB and ADEPT-first verify only already-requested transactions; no provider
traffic, duplicate PNB POST, Adobe mutation, EPUB deletion or log secrets.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

from dataclasses import replace
import json

from cifereca.core.loan_return_verification import _paris_loan_presence
from tests.test_core_0982_loan_return_reconciliation import (
    _library, _loan, Session,
)
from tests.test_core_0984_adept_first_paris_reconcile import setup as adept_setup


def test_pnb_same_id_in_syracuse_does_not_confirm_dilicom_return(tmp_path):
    loan = _loan()
    # Exact upstream provider borrower/credential and loan ID, but a
    # DIFFERENT supplier branch. The inventory does not identify this right.
    other_branch = replace(
        loan, provider="SYRACUSE", medium="physical",
        title="PRIVATE_SYRACUSE_TITLE",
    )
    session = Session([other_branch])
    service = _library(tmp_path, session=session)
    try:
        result = service.return_digital(loan)
        assert result["status"] == "verification_unavailable"
        assert result["verification"] == "unavailable"
        assert result["provider_inventory_verified"] is False
        assert session.posts == [(loan.id, "linked-b")]
        assert session.checks == ["linked-b"]
        assert "PRIVATE_SYRACUSE_TITLE" not in json.dumps(result)
        # Retry is verification-only, never a second borrower POST.
        again = service.verify_return_digital(loan)
        assert again["status"] == "verification_unavailable"
        assert again["provider_inventory_verified"] is False
        assert len(session.posts) == 1
        # Later exact positive evidence establishes presence without POST.
        session.inventory = [loan, other_branch]
        current = service.verify_return_digital(loan)
        assert current["status"] == "still_active"
        assert len(session.posts) == 1
        # A typed DILICOM pagination attestation is required before
        # an unrelated row can prove the requested loan absent.
        session.inventory = [replace(loan, id="unrelated")]
        attest_paris_dilicom_complete(session, credential_id="paris-a")
        confirmed = service.verify_return_digital(loan)
        assert confirmed["status"] == "provider_return_confirmed"
        assert confirmed["provider_inventory_verified"] is True
        assert len(session.posts) == 1
    finally:
        service.close()


def test_adept_first_same_id_in_other_paris_branch_is_unverified(tmp_path):
    service, loan, paris = adept_setup(tmp_path)
    paris.inventory = [replace(
        loan, provider="SYRACUSE", medium="physical",
        title="PRIVATE_CROSS_BRANCH",
    )]
    try:
        result = service.verify_paris_return_inventory(loan)
        assert result == {
            "status": "verification_unavailable",
            "verification": "unavailable",
        }
        assert paris.posts == 0
        assert paris.accounts_read == ["linked-123"]
        assert "PRIVATE_CROSS_BRANCH" not in json.dumps(result)
        paris.inventory = [loan, *paris.inventory]
        present = service.verify_paris_return_inventory(loan)
        assert present["status"] == "still_active"
        assert paris.posts == 0
    finally:
        service.close()


def test_presence_rule_is_exact_branch_and_does_not_infer_absence():
    loan = _loan()
    unrelated = replace(loan, id="other-id")
    other_branch = replace(loan, provider="SYRACUSE")
    assert _paris_loan_presence([unrelated], loan) is False
    assert _paris_loan_presence([other_branch], loan) is None
    assert _paris_loan_presence([loan, other_branch], loan) is True
    assert _paris_loan_presence([], loan) is False
    # Same supplier label does not override a conflicting medium.
    assert _paris_loan_presence([
        replace(loan, medium="physical"),
    ], loan) is None
    assert _paris_loan_presence([
        replace(loan, medium="physical"), loan,
    ], loan) is True



def test_pnb_same_id_and_provider_but_other_medium_is_not_presence(tmp_path):
    loan = _loan()
    other_medium = replace(
        loan, medium="physical", title="SYNTHETIC_PHYSICAL_ONLY",
    )
    session = Session([other_medium])
    service = _library(tmp_path, session=session)
    try:
        result = service.return_digital(loan)
        assert result["status"] == "verification_unavailable"
        assert result["verification"] == "unavailable"
        assert result["provider_inventory_verified"] is False
        assert "SYNTHETIC_PHYSICAL_ONLY" not in json.dumps(result)
        assert session.posts == [(loan.id, "linked-b")]
        assert service.verify_return_digital(loan)["status"] == "verification_unavailable"
        assert len(session.posts) == 1
        # An exact digital observation supersedes only the same branch.
        session.inventory = [other_medium, loan]
        assert service.verify_return_digital(loan)["status"] == "still_active"
        assert len(session.posts) == 1
    finally:
        service.close()


def test_adept_first_same_provider_id_different_medium_is_unverified(tmp_path):
    service, loan, paris = adept_setup(tmp_path)
    paris.inventory = [replace(
        loan, medium="physical", title="SYNTHETIC_DIFFERENT_MEDIUM",
    )]
    try:
        result = service.verify_paris_return_inventory(loan)
        assert result == {
            "status": "verification_unavailable",
            "verification": "unavailable",
        }
        assert paris.posts == 0
        assert "SYNTHETIC_DIFFERENT_MEDIUM" not in json.dumps(result)
        paris.inventory = [loan, *paris.inventory]
        assert service.verify_paris_return_inventory(loan)["status"] == "still_active"
        assert paris.posts == 0
    finally:
        service.close()
