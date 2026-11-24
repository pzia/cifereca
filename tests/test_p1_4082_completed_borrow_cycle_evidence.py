"""#4082: offline qualification is exact, fresh and fail closed."""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import pytest
from cifereca.models import Loan
from cifereca.plugin_api.loan_inventory import (
    LoanBranchCoverage, LoanInventoryScope, LoanInventorySnapshot,
)
from cifereca.core.borrow_cycle_evidence import completed_manual_borrow_cycle
from cifereca.providers.base import ManualBorrowPolicy
from tests.test_p1_4082_bnf_borrow_claim_observation import _account, _item

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)


def claim(**kw):
    row = dict(schema=1, operation="loan.borrow", status="confirmed",
               request_accepted=True, verification="positive_active_loan",
               operation_id="first", verified_loan_id="old-loan")
    return {**row, **kw}


def loan(account, *, ident="other-loan", edition="other-edition"):
    return Loan(id=ident, provider="BNF_PRET", medium="digital",
                record_id=edition, title="Synthetic", state="active",
                source_provider=account.provider_id,
                source_credential=account.credential_id,
                source_account_id=account.unique_id, identity_id=account.identity_id)


def snapshot(account, *, status="success", complete=True, stamp=None, loans=None):
    return LoanInventorySnapshot(
        scope=LoanInventoryScope.for_account(account),
        loans=tuple([loan(account)] if loans is None else loans),
        status=status, branches=(LoanBranchCoverage(
            "bnf_html", "digital", "complete" if complete else "partial",
            True if complete else None,
        ),), observed_at=stamp or NOW.isoformat(),
        reason_code=None if status == "success" else "inventory_not_exhaustive",
    )


def decide(**kwargs):
    a = _account()
    return completed_manual_borrow_cycle(**{
        **dict(previous_claim=claim(), snapshot=snapshot(a), account=a,
               item=_item(a), branch="bnf_html", now=NOW), **kwargs,
    })


def test_exact_completed_confirmed_cycle_can_be_qualified():
    assert decide()
    assert ManualBorrowPolicy(
        docbase="BNF_PRET", reborrow_inventory_branch="bnf_html",
    ).reborrow_inventory_branch == "bnf_html"


@pytest.mark.parametrize("change", [
    {"status": "request_sent"}, {"status": "submitted"},
    {"status": "verification_unavailable"}, {"status": "blocked"},
    {"request_accepted": False}, {"verification": "unavailable"},
    {"verified_loan_id": ""}, {"operation_id": ""},
])
def test_prior_not_proven_ended_cannot_be_reused(change):
    assert not decide(previous_claim=claim(**change))


def test_incomplete_stale_or_future_inventory_fails_closed():
    a = _account()
    for snap in (
        snapshot(a, complete=False),
        snapshot(a, status="partial", complete=False),
        snapshot(a, stamp=(NOW-timedelta(minutes=5)).isoformat()),
        snapshot(a, stamp=(NOW+timedelta(minutes=1)).isoformat()),
    ):
        assert not decide(snapshot=snap)


def test_complete_empty_bnf_inventory_qualifies_finished_cycle():
    # Owner-approved: a producer-qualified complete empty 200 activity widget
    # establishes prior-loan absence, not a guaranteed quota or POST success.
    a = _account()
    assert decide(snapshot=snapshot(a, loans=[]))
    assert decide(snapshot=snapshot(a, loans=[loan(a)]))
    assert not decide(snapshot=snapshot(a, loans=[], complete=False))
    assert not decide(snapshot=snapshot(a, loans=[], status="partial", complete=False))


def test_previous_loan_or_same_edition_still_active_is_not_ended():
    a = _account()
    for row in (loan(a, ident="old-loan"), loan(a, edition=_item(a).record_id)):
        assert not decide(snapshot=snapshot(a, loans=[row]))


def test_foreign_account_and_uncovered_branch_fail():
    assert not decide(snapshot=snapshot(replace(_account(), credential_id="other")))
    assert not decide(branch="another")


def test_frozen_snapshot_mutations_are_rechecked():
    a = _account()
    snap = snapshot(a)
    object.__setattr__(snap, "status", "partial")
    assert not decide(snapshot=snap)
    snap = snapshot(a)
    object.__setattr__(snap.branches[0], "pagination_complete", False)
    assert not decide(snapshot=snap)
