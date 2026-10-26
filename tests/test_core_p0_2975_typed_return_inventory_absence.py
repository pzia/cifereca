"""#2975/#3119: Paris return verification consumes atomic source coverage.

Provider reads are synthetic. No actual PNB POST, ADEPT call or rights changes.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.plugin_api.loan_inventory import (
    LoanBranchCoverage, LoanInventoryScope, LoanInventorySnapshot,
)
from tests.test_core_0982_loan_return_reconciliation import Session, _library, _loan
from tests.test_core_0984_adept_first_paris_reconcile import setup as adept_setup


def _enable_typed(session, *, status="success", loans=(), coverage="unknown"):
    session.provider_id = "paris"
    session.credential_id = "paris-a"
    session.capabilities = SimpleNamespace(loans=True)
    calls = []
    def inventory(account):
        calls.append(account.unique_id)
        branches = (
            LoanBranchCoverage(
                "dilicom", "digital", coverage,
                pagination_complete=True if coverage == "complete" else None,
            ),
            LoanBranchCoverage("syracuse", "physical", "unknown"),
        )
        return LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(account),
            loans=tuple(loans), status=status, branches=branches,
        )
    session.loan_inventory_snapshot = inventory
    session.loans = lambda _: pytest.fail("typed result must not invoke legacy GET")
    return calls


@pytest.mark.parametrize(("status", "coverage"), [
    ("success", "unknown"),
    ("success", "partial"),
    ("partial", "complete"),
    ("failed", "unknown"),
    ("cancelled", "unknown"),
])
def test_empty_typed_read_never_proves_return_without_complete_dilicom(
    tmp_path, status, coverage,
):
    loan = _loan()
    session = Session([])
    session.last_loan_errors = []  # Stale side channel must not promote typed claims.
    calls = _enable_typed(session, status=status, coverage=coverage)
    service = _library(tmp_path, session=session)
    try:
        result = service.return_digital(loan)
        assert result["status"] == "verification_unavailable"
        assert result["verification"] == "unavailable"
        assert result["provider_inventory_verified"] is False
        assert session.posts == [(loan.id, "linked-b")]
        assert calls == ["linked-b"]
        again = service.verify_return_digital(loan)
        assert again["status"] == "verification_unavailable"
        assert calls == ["linked-b", "linked-b"]
        assert len(session.posts) == 1
    finally:
        service.close()


def test_exact_positive_typed_loan_remains_active_despite_partial_sibling_branch(tmp_path):
    loan = _loan()
    session = Session([])
    calls = _enable_typed(session, status="partial", loans=(loan,))
    service = _library(tmp_path, session=session)
    try:
        outcome = service.return_digital(loan)
        assert outcome["status"] == "still_active"
        assert outcome["verification"] == "still_present"
        assert outcome["provider_inventory_verified"] is False
        assert calls == ["linked-b"]
        assert len(session.posts) == 1
    finally:
        service.close()


def test_explicit_complete_typed_dilicom_branch_can_attest_absence(tmp_path):
    loan = _loan()
    session = Session([])
    calls = _enable_typed(session, coverage="complete")
    service = _library(tmp_path, session=session)
    try:
        result = service.return_digital(loan)
        assert result["status"] == "provider_return_confirmed"
        assert result["provider_inventory_verified"] is True
        assert result["verification"] == "provider_absent"
        assert calls == ["linked-b"]
        assert len(session.posts) == 1
    finally:
        service.close()


@pytest.mark.parametrize("change", ["session", "context_epoch", "account", "foreign_scope"])
def test_inflight_typed_return_rejects_changed_context_before_confirming_absence(
    tmp_path, change,
):
    loan = _loan()
    session = Session([])
    session.credential_id = "paris-a"
    session.capabilities = SimpleNamespace(loans=True)
    service = _library(tmp_path, session=session)
    original_accounts = service.accounts
    original_session = service._sessions["paris::paris-a"]
    calls = []
    def snapshot(account):
        calls.append(account.unique_id)
        result = LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(account), loans=(),
            status="success", branches=(
                LoanBranchCoverage("dilicom", "digital", "complete", pagination_complete=True),
            ),
        )
        if change == "session":
            service._sessions["paris::paris-a"] = Session([])
        elif change == "context_epoch":
            service._account_context_epoch += 1
        elif change == "account":
            service.accounts = [replace(row) for row in original_accounts]
        else:
            return LoanInventorySnapshot(
                scope=LoanInventoryScope("other", "paris", "another-credential", "linked-b"),
                loans=(), status="success", branches=result.branches,
            )
        return result
    session.loan_inventory_snapshot = snapshot
    session.loans = lambda _: pytest.fail("legacy second read forbidden")
    try:
        result = service.return_digital(loan)
        assert result["status"] == "verification_unavailable"
        assert result["provider_inventory_verified"] is False
        assert calls == ["linked-b"]
        assert session.posts == [(loan.id, "linked-b")]
    finally:
        service._sessions["paris::paris-a"] = original_session
        service.accounts = original_accounts
        service.close()


@pytest.mark.parametrize("coverage,expected", [
    ("unknown", "verification_unavailable"),
    ("complete", "provider_return_confirmed"),
])
def test_adept_first_uses_same_typed_absence_gate(tmp_path, coverage, expected):
    service, loan, paris = adept_setup(tmp_path)
    calls = _enable_typed(paris, coverage=coverage)
    # This synthetic source uses a different stored credential than _library.
    paris.credential_id = loan.source_credential
    try:
        result = service.verify_paris_return_inventory(loan)
        assert result["status"] == expected
        assert calls == [loan.source_account_id]
        assert paris.posts == 0
    finally:
        service.close()
