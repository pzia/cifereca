"""#6002: stage a *non-authoritative*, typed digital inventory observation.

A provider's LoanInventorySnapshot is never itself a durable, all-branch
Core receipt. In particular, success for one provider branch must not become
a quota estimate, and loaded loans are not proof of a complete inventory.

This pure adapter deliberately has NO parameter to mark a receipt committed.
A later Core transaction boundary must verify origin, expected branches,
session/identity epoch and durable storage before upgrading it. Home cannot
consume a provisional receipt as available headroom.
"""
from __future__ import annotations

from datetime import datetime

from ..plugin_api.loan_inventory import (
    INVENTORY_CONTRACT, LoanBranchCoverage, LoanInventoryScope,
    LoanInventorySnapshot, _loan_matches_scope, _REASON_CODES,
)
from .digital_loan_remaining_policy import (
    DigitalCapacityScope, DigitalInventoryReceipt,
)


def _stamp(observed_at: object) -> datetime | None:
    # Reapply the original LoanInventorySnapshot timestamp contract even
    # when a frozen upstream envelope was replaced in memory after __init__.
    if type(observed_at) is not str or not 20 <= len(observed_at) <= 40:
        return None
    try:
        value = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    if (value.tzinfo is None or value.utcoffset() is None
            or not 2000 <= value.year <= 2100):
        return None
    return value


def stage_uncommitted_digital_inventory(
    scope: DigitalCapacityScope,
    snapshot: LoanInventorySnapshot, *,
    expected_upstreams: tuple[str, ...],
    epoch: int,
) -> DigitalInventoryReceipt | None:
    """Project one source-scoped typed snapshot, never declare a durable sync.

    The expected upstream branches must be supplied from an independently
    installed, trusted descriptor, not from the snapshot's self-reported list.
    Even when every branch claims completeness the result remains
    durably_committed=False; it cannot feed positive remaining availability.
    """
    if (type(scope) is not DigitalCapacityScope
            or type(snapshot) is not LoanInventorySnapshot
            or type(epoch) is not int or epoch < 0
            or type(expected_upstreams) is not tuple
            or not 1 <= len(expected_upstreams) <= 32
            or any(type(upstream) is not str
                   or not 1 <= len(upstream) <= 48
                   or not upstream.isascii()
                   or not upstream[0].isalpha()
                   or any(not (char.islower() or char.isdigit() or char in "_-")
                          for char in upstream)
                   for upstream in expected_upstreams)
            or len(set(expected_upstreams)) != len(expected_upstreams)):
        raise ValueError("digital_inventory_staging_invalid_contract")

    # A frozen upstream envelope is still an untrusted Python object:
    # object.__setattr__ and nested mutable Loan rows can replace its
    # branches/contract AFTER LoanInventorySnapshot.__post_init__. Before
    # interpreting a claimed complete pagination, validate the *current*
    # complete envelope again. In particular, a duplicated upstream with
    # conflicting complete/partial claims must not be collapsed by the
    # dictionary projection below (last-wins would falsely claim success).
    if (snapshot.contract != INVENTORY_CONTRACT
            or type(snapshot.scope) is not LoanInventoryScope
            or type(snapshot.loans) is not tuple
            or len(snapshot.loans) > 10000
            or type(snapshot.branches) is not tuple
            or len(snapshot.branches) > 32
            or type(snapshot.status) is not str
            or snapshot.status not in ("success", "partial", "failed", "cancelled")
            or (snapshot.reason_code is not None
                and (type(snapshot.reason_code) is not str
                     or snapshot.reason_code not in _REASON_CODES))
            or any(type(row) is not LoanBranchCoverage
                   for row in snapshot.branches)):
        return None
    try:
        scope.__post_init__()
        snapshot.scope.__post_init__()
        for row in snapshot.branches:
            # Rebuilding the immutable branch verifies type, medium,
            # completeness/pagination and its closed reason code even after
            # a malicious/buggy upstream mutated its frozen fields.
            LoanBranchCoverage(
                upstream=row.upstream, medium=row.medium,
                completeness=row.completeness,
                pagination_complete=row.pagination_complete,
                reason_code=row.reason_code,
            )
    except (ValueError, TypeError, AttributeError):
        return None
    if len({(row.upstream, row.medium) for row in snapshot.branches}) != len(
        snapshot.branches
    ):
        return None

    upstream_scope = snapshot.scope
    if (scope.identity_id != upstream_scope.identity_id
            or scope.provider_id != upstream_scope.provider_id
            or scope.credential_id != upstream_scope.credential_id
            or scope.account_id != upstream_scope.account_id
            or scope.borrower_id != upstream_scope.account_id):
        # A credential alone is not a borrower; missing exact account scope
        # cannot be normalized to the profile's first or primary account.
        return None
    observed_at = _stamp(snapshot.observed_at)
    if observed_at is None:
        return None

    # Snapshot.loans is a tuple but the legacy Loan rows are mutable. Validate
    # their exact scope *again* before copying only immutable identifiers;
    # an upstream row modified since the envelope was built is not evidence.
    seen_rows = []
    active: set[str] = set()
    # loan.id alone is not a source/edition key across upstream branches.
    # Identical replayed rows can safely deduplicate; conflicting rows
    # sharing an ID cannot prove how many *distinct* loans are active.
    # Since DigitalInventoryReceipt counts IDs, any such collision must
    # revoke completeness rather than silently underestimate occupation.
    loan_identity_by_id: dict[str, tuple[object, ...]] = {}
    trustworthy_states = True
    expected = set(expected_upstreams)
    for loan in snapshot.loans:
        if not _loan_matches_scope(loan, upstream_scope):
            return None
        fields = (
            loan.id, loan.identity_id, loan.source_provider,
            loan.source_credential, loan.source_account_id,
            loan.medium, loan.state, loan.provider, loan.record_id,
        )
        seen_rows.append(fields)
        loan_identity = (
            loan.provider, loan.record_id, loan.medium, loan.state,
            loan.source_account_id,
        )
        previous_identity = loan_identity_by_id.setdefault(
            loan.id, loan_identity,
        )
        if previous_identity != loan_identity:
            # A reused ID may denote independent loans on two branches,
            # or a contradictory active/returned record. Both are unsafe
            # to subtract from a total quota as one loan.
            trustworthy_states = False
        if (type(loan.state) is not str
                or loan.state not in ("active", "returned", "expired", "inactive")
                or loan.medium == "unknown"):
            trustworthy_states = False
        if loan.medium == "digital":
            # A complete list of declared branch receipts cannot qualify
            # digital rows from an *undeclared* upstream. In particular,
            # a digital loan labelled SYRACUSE with only DILICOM/OVERDRIVE
            # coverage is not evidence that all digital loans were visited.
            # Only a real matching upstream can authorize future subtraction.
            if (type(loan.provider) is not str
                    or not loan.provider.isascii()
                    or loan.provider.casefold() not in expected):
                trustworthy_states = False
            if loan.state == "active":
                active.add(loan.id)
    if tuple(seen_rows) != tuple((
        loan.id, loan.identity_id, loan.source_provider,
        loan.source_credential, loan.source_account_id,
        loan.medium, loan.state, loan.provider, loan.record_id,
    ) for loan in snapshot.loans):
        return None

    declared = {
        row.upstream: row
        for row in snapshot.branches if type(row) is LoanBranchCoverage
        and row.medium == "digital"
    }
    observed = set(declared)
    # Unknown-medium branches could contain digital loans and must not be
    # ignored merely because every declared digital branch is complete.
    uncertain_medium = any(
        type(row) is not LoanBranchCoverage or row.medium == "unknown"
        for row in snapshot.branches
    )
    complete = (
        snapshot.status == "success"
        # A success envelope carrying a negative/unknown reason is
        # contradictory, even if its branch fields still say "complete".
        and snapshot.reason_code is None
        and trustworthy_states and not uncertain_medium
        and observed == expected
        and all(
            declared[name].completeness == "complete"
            and declared[name].pagination_complete is True
            for name in expected_upstreams
        )
    )
    status = (
        snapshot.status if snapshot.status in ("failed", "cancelled")
        else "success" if complete else "partial"
    )
    return DigitalInventoryReceipt(
        scope=scope, active_loan_ids=tuple(sorted(active)),
        observed_at=observed_at, epoch=epoch,
        status=status, pagination_complete=complete,
        durably_committed=False,
    )


__all__ = ["stage_uncommitted_digital_inventory"]
