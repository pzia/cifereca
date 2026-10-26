"""P0 #2975: only complete scoped digital inventories prove loan absence.

Offline fake provider snapshots and SQLite, no ADEPT or network.
"""
from __future__ import annotations

import pytest
from cifereca.core.sync_coordination import _complete_digital_loan_scope
from cifereca.plugin_api import (
    LoanBranchCoverage, LoanInventoryScope, LoanInventorySnapshot,
)
from tests.test_core_p1_3119_3122_opening_typed_inventory import (
    account, attached, loan, result_for,
)
from tests.test_core_p0_2975_manual_source_loan_parity import (
    _fixture as explicit_fixture, _loan as explicit_loan, _right,
)
from tests.test_sync_state_contract import _service


def snapshot(a, loans=(), *, status="success", coverage="complete", extra=()):
    return LoanInventorySnapshot(
        scope=LoanInventoryScope.for_account(a), loans=tuple(loans),
        status=status, branches=(
            LoanBranchCoverage(
                upstream="dilicom" if a.provider_id == "paris" else "bnf_html",
                medium="digital", completeness=coverage,
                pagination_complete=True if coverage == "complete" else None,
            ), *extra,
        ),
    )


@pytest.mark.parametrize("provider", ["paris", "bnf-pret"])
def test_opening_realizes_only_exact_complete_borrower(
    tmp_path, provider,
):
    service = _service(tmp_path)
    try:
        a = account(provider, credential="first",
                    borrower="" if provider == "paris" else "reader-a")
        b = account(provider, credential="second", borrower="reader-b")
        service.accounts = [a, b]
        missing, kept, foreign = loan(a, "missing"), loan(a, "kept"), loan(b, "foreign")
        service._persist_active_loans("default", [missing, kept, foreign])
        first, _ = attached(service, a, observed=[])
        first.loan_inventory_snapshot = lambda _: snapshot(a, [kept])
        attached(service, b, observed=[], status="partial")
        outcome = service.opening_sync()
        rights = {r["loan_id"]: r for r in service.storage.entitlements()}
        assert rights[missing.id]["state"] == "realized"
        assert rights[kept.id]["state"] == "active"
        assert rights[foreign.id]["state"] == "active"
        assert result_for(outcome)["realized"] == 1
        assert result_for(service.opening_sync())["realized"] == 0
    finally:
        service.close()


@pytest.mark.parametrize("condition", ["unknown", "partial", "other_branch",
                                       "unclassified", "failed"])
def test_unproven_typed_coverage_cannot_close_existing_loan(tmp_path, condition):
    a = account()
    coverage, status, extra = "complete", "success", ()
    if condition in ("unknown", "partial"):
        coverage = condition
    elif condition == "other_branch":
        extra = (LoanBranchCoverage(upstream="other", medium="digital"),)
    elif condition == "unclassified":
        extra = (LoanBranchCoverage(upstream="other", medium="unknown"),)
    else:
        status = "failed"
    evidence = snapshot(a, status=status, coverage=coverage, extra=extra)
    assert _complete_digital_loan_scope(a, evidence) is None
    service = _service(tmp_path)
    try:
        service.accounts = [a]
        prior = loan(a, "prior")
        service._persist_active_loans("default", [prior])
        first, _ = attached(service, a, observed=[])
        first.loan_inventory_snapshot = lambda _: evidence
        service.opening_sync()
        assert next(r for r in service.storage.entitlements()
                    if r["loan_id"] == prior.id)["state"] == "active"
    finally:
        service.close()


def test_explicit_source_sync_uses_same_scope_and_is_idempotent(tmp_path, monkeypatch):
    service, primary, _, _, _, session = explicit_fixture(tmp_path, monkeypatch)
    try:
        old = explicit_loan(item="old")
        service._persist_active_loans("default", [old])
        session.loan_inventory_snapshot = lambda _: snapshot(primary)
        assert service.synchronize_account_source(primary)["loans"] == 0
        assert _right(service, "primary", "old")["state"] == "realized"
        assert service.synchronize_account_source(primary)["loans"] == 0
    finally:
        service.close()
