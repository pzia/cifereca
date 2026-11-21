"""P1 #6002: a typed upstream inventory envelope can be mutated after init.

Neither its frozen dataclass marker nor a final duplicate branch dictionary
may upgrade an in-memory provider claim to a provisional *complete* receipt.
This test does no provider, FS, SQLite or network I/O.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from cifereca.core.digital_loan_inventory_staging import (
    stage_uncommitted_digital_inventory,
)
from cifereca.core.digital_loan_remaining_policy import (
    evaluate_digital_remaining,
)
from cifereca.plugin_api.loan_inventory import (
    LoanBranchCoverage,
)
from tests.test_core_p1_6002_typed_inventory_staging import (
    ACCOUNT, COMPLETE, NOW, SCOPE, loan, snapshot,
)


def stage(read, *, scope=SCOPE):
    return stage_uncommitted_digital_inventory(
        scope, read, expected_upstreams=("dilicom", "overdrive"), epoch=7,
    )


def altered(**changes):
    read = snapshot()
    for key, value in changes.items():
        object.__setattr__(read, key, value)
    return read


def test_original_complete_claim_remains_only_provisional():
    staged = stage(snapshot())
    assert staged.status == "success"
    assert staged.pagination_complete
    assert staged.durably_committed is False
    assert evaluate_digital_remaining(
        SCOPE, now=NOW, epoch=7, max_age=timedelta(hours=6),
        inventory=staged, manual_total=4,
    ).remaining is None


def test_duplicate_branch_after_frozen_envelope_cannot_overwrite_partial_claim():
    # The first and last digital records are complete; an injected partial
    # duplicate in between must not disappear when converted to dict.
    read = altered(branches=(
        COMPLETE[0],
        LoanBranchCoverage("overdrive", "digital", "partial", False),
        COMPLETE[1],
    ))
    assert stage(read) is None
    # Even two *identical* duplicates are not independent branch evidence.
    assert stage(altered(branches=(
        COMPLETE[0], replace(COMPLETE[0]), COMPLETE[1],
    ))) is None


@pytest.mark.parametrize("bad", [
    [], (COMPLETE[0],) * 33,
    ("dilicom", COMPLETE[1]), None,
])
def test_replaced_branches_must_still_be_bounded_immutable_typed_tuple(bad):
    assert stage(altered(branches=bad)) is None


@pytest.mark.parametrize("change", [
    {"medium": "unknown-medium"},
    {"completeness": "complete", "pagination_complete": False},
    {"reason_code": "arbitrary-false-proof"},
    {"upstream": "BAD-UPSTREAM"},
])
def test_mutated_immutable_branch_is_revalidated_before_completeness(change):
    branch = replace(COMPLETE[0])
    for name, value in change.items():
        object.__setattr__(branch, name, value)
    read = altered(branches=(branch, COMPLETE[1]))
    assert stage(read) is None


@pytest.mark.parametrize("changes", [
    {"contract": "forged-contract"},
    {"status": "unexpected"},
    {"status": 42},
    {"scope": {"identity_id": "alice"}},
    {"loans": [loan(1)]},
    {"loans": (loan(1),) * 10001},
])
def test_frozen_envelope_field_replacement_fails_closed(changes):
    assert stage(altered(**changes)) is None


def test_mutated_source_scope_requires_original_alias_invariants():
    account = replace(ACCOUNT)
    object.__setattr__(account, "primary_account_alias", "borrower-a")
    read = altered(scope=account)
    assert stage(read) is None


def test_mutated_internal_scope_cannot_leak_into_some_other_borrower():
    foreign = replace(SCOPE)
    object.__setattr__(foreign, "credential_id", None)
    assert stage(snapshot(), scope=foreign) is None


def test_valid_extra_physical_branch_still_allows_provisional_digital_claim():
    # Physical branches do not subtract from the independently proven
    # digital quota; only a source of UNKNOWN medium is blocking.
    physical = LoanBranchCoverage("syracuse", "physical", "partial", False)
    positive = stage(altered(branches=(*COMPLETE, physical)))
    assert positive.status == "success"
    assert positive.pagination_complete
    assert positive.durably_committed is False


def test_unknown_medium_is_partial_not_a_silent_complete_claim():
    unknown = LoanBranchCoverage("branch-unknown", "unknown")
    partial = stage(altered(branches=(*COMPLETE, unknown)))
    assert partial.status == "partial"
    assert partial.pagination_complete is False
    assert partial.durably_committed is False



@pytest.mark.parametrize("timestamp", [
    "1999-12-31T23:00:00+00:00",
    "2101-01-01T00:00:00+00:00",
    "2026-11-15T07:00:00",
    "2026-11-15",
    42,
    "2026-11-15T07:00:00+00:00" + " " * 40,
])
def test_mutated_inventory_timestamp_cannot_qualify_an_old_envelope(timestamp):
    # The typed constructor validates dates, but a frozen object can be
    # changed after that validation. The staging read must recheck them.
    assert stage(altered(observed_at=timestamp)) is None


@pytest.mark.parametrize("reason", ["read_failed", "cancelled",
                                    "inventory_not_exhaustive", "unknown"])
def test_success_with_failure_or_unknown_reason_is_not_complete(reason):
    read = altered(reason_code=reason)
    result = stage(read)
    assert result is not None
    assert result.status == "partial"
    assert result.pagination_complete is False
    assert result.durably_committed is False
    assert evaluate_digital_remaining(
        SCOPE, now=NOW, epoch=7, max_age=timedelta(hours=6),
        inventory=result, manual_total=4,
    ).remaining is None


@pytest.mark.parametrize("reason", ["fake_complete", 0, True, [], {}])
def test_mutated_unrecognized_inventory_reason_is_rejected(reason):
    assert stage(altered(reason_code=reason)) is None


def test_partially_observed_snapshot_keeps_a_valid_closed_reason():
    read = altered(status="partial", reason_code="pagination_incomplete")
    result = stage(read)
    assert result is not None
    assert result.status == "partial"
    assert result.pagination_complete is False
    assert result.durably_committed is False
