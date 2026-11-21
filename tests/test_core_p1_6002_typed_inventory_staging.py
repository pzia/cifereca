"""P1 #6002 — provisional typed inventory is NOT a durable quota receipt.

Fixtures intentionally resemble the provider's optional loan.inventory/1
envelope. No network, account switch, Sync, SQLite write or quota authority.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from cifereca.core.digital_loan_inventory_staging import (
    stage_uncommitted_digital_inventory as stage,
)
from cifereca.core.digital_loan_remaining_policy import (
    DigitalCapacityScope, evaluate_digital_remaining,
)
from cifereca.models import Loan
from cifereca.plugin_api.loan_inventory import (
    LoanInventoryScope, LoanInventorySnapshot, LoanBranchCoverage,
)


NOW = datetime(2026, 11, 15, 7, 0, tzinfo=timezone.utc)
ACCOUNT = LoanInventoryScope("alice", "paris", "card-a", "reader-a")
SCOPE = DigitalCapacityScope("alice", "paris", "card-a", "reader-a", "reader-a")
COMPLETE = (
    LoanBranchCoverage("dilicom", "digital", "complete", True),
    LoanBranchCoverage("overdrive", "digital", "complete", True),
)


def loan(number, *, state="active", medium="digital", account="reader-a"):
    return Loan(
        id=f"loan-{number}", provider="DILICOM", medium=medium,
        record_id=f"edition-{number}", title="Synthétique",
        identity_id="alice", source_provider="paris",
        source_credential="card-a", source_account_id=account, state=state,
    )


def snapshot(*, rows=None, branches=COMPLETE, status="success",
             observed_at="2026-11-15T07:00:00+00:00"):
    return LoanInventorySnapshot(
        scope=ACCOUNT,
        loans=tuple(rows if rows is not None else (
            loan(1), loan(2), loan(1), loan(3, medium="physical"),
        )),
        branches=tuple(branches), status=status,
        observed_at=observed_at,
    )


def staging(**kwargs):
    return stage(
        kwargs.pop("scope", SCOPE), kwargs.pop("receipt", snapshot()),
        expected_upstreams=kwargs.pop(
            "expected_upstreams", ("dilicom", "overdrive"),
        ),
        epoch=kwargs.pop("epoch", 7),
        **kwargs,
    )


def test_all_digital_branches_complete_stage_only_uncommitted_active_ids():
    result = staging()
    assert result.scope == SCOPE
    assert result.status == "success"
    assert result.pagination_complete is True
    assert result.active_loan_ids == ("loan-1", "loan-2")
    assert result.durably_committed is False
    assert result.epoch == 7
    # Never expose a positive number on Home from provider claims alone.
    decision = evaluate_digital_remaining(
        SCOPE, now=NOW, epoch=7, max_age=timedelta(hours=6),
        manual_total=4, inventory=result,
    )
    assert (decision.status, decision.remaining, decision.eligibility) == (
        "unknown", None, "not_verified",
    )


@pytest.mark.parametrize("branches", [
    (LoanBranchCoverage("dilicom", "digital", "complete", True),),
    (LoanBranchCoverage("overdrive", "digital", "complete", True),),
    (LoanBranchCoverage("dilicom", "digital", "partial", False),
     COMPLETE[1]),
    (COMPLETE[0],
     LoanBranchCoverage("overdrive", "digital", "unknown", None)),
    (*COMPLETE,
     LoanBranchCoverage("third", "digital", "complete", True)),
    (*COMPLETE,
     LoanBranchCoverage("unverified", "unknown", "unknown")),
])
def test_incomplete_or_unexpected_branch_never_produces_complete_receipt(branches):
    result = staging(receipt=snapshot(branches=branches))
    assert result.status == "partial"
    assert result.pagination_complete is False
    assert result.durably_committed is False
    decision = evaluate_digital_remaining(
        SCOPE, now=NOW, epoch=7, max_age=timedelta(hours=1),
        manual_total=4, inventory=result,
    )
    assert decision.remaining is None


@pytest.mark.parametrize("status", ["partial", "failed", "cancelled"])
def test_supplier_success_is_not_inferred_from_any_other_status(status):
    result = staging(receipt=snapshot(status=status))
    assert result.status == status
    assert result.pagination_complete is False
    assert result.durably_committed is False


@pytest.mark.parametrize("rows", [
    (loan(1, state="pending"),),
    (loan(1, state="overdue"),),
    (loan(1, medium="unknown"),),
    (loan(1, state=None),),
    (loan(1, state=False),),
])
def test_ambiguous_loan_state_or_medium_revokes_completeness(rows):
    result = staging(receipt=snapshot(rows=rows))
    assert result.status == "partial"
    assert result.pagination_complete is False
    assert result.durably_committed is False


@pytest.mark.parametrize("scoped", [
    replace(SCOPE, identity_id="bob"),
    replace(SCOPE, provider_id="bnf"),
    replace(SCOPE, credential_id="card-b"),
    replace(SCOPE, account_id="reader-b"),
    replace(SCOPE, borrower_id="reader-b"),
])
def test_other_identity_account_or_borrower_never_inherits_inventory(scoped):
    assert staging(scope=scoped) is None


def test_mutated_loan_after_snapshot_creation_invalidates_stage():
    receipt = snapshot()
    receipt.loans[0].source_account_id = "other-reader"
    assert staging(receipt=receipt) is None


def test_missing_or_naive_provider_timestamp_never_becomes_receipt():
    assert staging(receipt=snapshot(observed_at=None)) is None
    naive = "2026-11-15T07:00:00.000"
    with pytest.raises(ValueError, match="invalid_loan_inventory_timestamp"):
        snapshot(observed_at=naive)
    # Defensive recheck in case an imported envelope was corrupted in memory.
    corrupted = snapshot()
    object.__setattr__(corrupted, "observed_at", naive)
    assert staging(receipt=corrupted) is None


@pytest.mark.parametrize("upstreams", [
    (), ("dilicom", "dilicom"), ("DILICOM",),
    ("dilicom", "overdrive", 123), tuple(f"branch{i}" for i in range(33)),
])
def test_ambiguous_or_malformed_authoritative_branch_list_is_rejected(upstreams):
    with pytest.raises(ValueError, match="digital_inventory_staging_invalid_contract"):
        staging(expected_upstreams=upstreams)


def test_explicit_primary_empty_id_is_not_a_wildcard():
    root_scope = LoanInventoryScope("alice", "paris", "card-a", "")
    receipt = LoanInventorySnapshot(
        scope=root_scope, loans=(loan(1, account=""),),
        branches=COMPLETE, observed_at="2026-11-15T07:00:00+00:00",
        status="success",
    )
    correct = replace(SCOPE, account_id="", borrower_id="")
    result = staging(scope=correct, receipt=receipt)
    assert result is not None
    assert result.status == "success" and result.durably_committed is False
    assert staging(scope=replace(correct, borrower_id="reader-a"), receipt=receipt) is None


def test_legacy_partial_snapshot_without_page_evidence_cannot_stage_availability():
    receipt = LoanInventorySnapshot(
        scope=ACCOUNT, loans=(loan(1),), status="partial",
        branches=(), observed_at=None,
    )
    assert staging(receipt=receipt) is None


@pytest.mark.parametrize("conflicting", [
    replace(loan(1), record_id="another-edition"),
    replace(loan(1), provider="OVERDRIVE"),
    loan(1, medium="physical"),
    loan(1, state="returned"),
    loan(1, state="expired"),
])
def test_reused_loan_id_with_other_edition_branch_or_state_is_not_complete(
    conflicting,
):
    """One ID in two different records is not proof of only one active loan."""
    receipt = snapshot(rows=(loan(1), conflicting, loan(2)))
    result = staging(receipt=receipt)
    assert result is not None
    assert result.status == "partial"
    assert result.pagination_complete is False
    assert result.durably_committed is False
    # Even with a complete typed pagination claim and a manual ceiling,
    # ambiguous duplicate IDs must never produce a positive N.
    decision = evaluate_digital_remaining(
        SCOPE, now=NOW, epoch=7, max_age=timedelta(hours=6),
        manual_total=4, inventory=result,
    )
    assert (decision.status, decision.remaining, decision.eligibility) == (
        "unknown", None, "not_verified",
    )


def test_exact_duplicate_loan_replay_stays_complete_and_counts_once():
    receipt = snapshot(rows=(loan(1), loan(1), loan(2)))
    result = staging(receipt=receipt)
    assert result.status == "success"
    assert result.pagination_complete is True
    assert result.active_loan_ids == ("loan-1", "loan-2")
    assert result.durably_committed is False
    assert evaluate_digital_remaining(
        SCOPE, now=NOW, epoch=7, max_age=timedelta(hours=6),
        manual_total=4, inventory=result,
    ).remaining is None


@pytest.mark.parametrize("upstream", [
    "SYRACUSE", "UNKNOWN", "PARIS", "", None, 42, "DILICOM/OVERDRIVE",
])
def test_undeclared_or_untyped_upstream_digital_loan_revokes_completeness(upstream):
    """Provider-supplied rows must match installed expected digital branches.

    Complete pagination labels alone cannot qualify a foreign digital loan.
    In particular a physical-only upstream cannot be silently counted in
    the digital borrower's allowance without matching branch coverage.
    """
    receipt = snapshot(rows=(
        loan(1), replace(loan(2), provider=upstream),
    ))
    staged = staging(receipt=receipt)
    assert staged is not None
    assert staged.status == "partial"
    assert staged.pagination_complete is False
    assert staged.durably_committed is False
    assert staged.active_loan_ids == ("loan-1", "loan-2")
    assert evaluate_digital_remaining(
        SCOPE, now=NOW, epoch=7, max_age=timedelta(hours=6),
        manual_total=4, inventory=staged,
    ).remaining is None


def test_two_declared_digital_upstreams_preserve_provisional_positive_rows():
    receipt = snapshot(rows=(
        loan(1),
        replace(loan(2), provider="OVERDRIVE"),
        loan(3, medium="physical"),
    ))
    staged = staging(receipt=receipt)
    assert staged is not None
    assert staged.status == "success"
    assert staged.pagination_complete is True
    assert staged.durably_committed is False
    assert staged.active_loan_ids == ("loan-1", "loan-2")
    assert evaluate_digital_remaining(
        SCOPE, now=NOW, epoch=7, max_age=timedelta(hours=6),
        manual_total=4, inventory=staged,
    ).remaining is None
