"""Optional, additive loan-inventory read contract (Core P1 #3119).

This is a statement made by a provider, not Core authorization to expire rights.
Consumers must verify scope, branch coverage and their own transaction epoch
before deriving any negative evidence. Legacy providers remain supported but
NEVER claim a complete inventory through this adapter.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from cifereca.models import Account, Loan
from .errors import Cancelled

InventoryStatus = Literal["success", "partial", "failed", "cancelled"]
InventoryCompleteness = Literal["complete", "partial", "unknown"]
INVENTORY_CONTRACT = "cifereca.loan.inventory/1"
_MAX_LOANS = 10_000
_MAX_BRANCHES = 32
_REASON_CODES = frozenset({
    "legacy_unverified", "inventory_not_exhaustive", "branch_unavailable",
    "pagination_incomplete", "read_failed", "cancelled", "unavailable",
    "scope_changed", "invalid_response", "unknown",
})


def _valid_token(value: object, *, empty: bool = False) -> bool:
    return (type(value) is str and len(value) <= 1024
            and (empty or bool(value)) and "\x00" not in value)


@dataclass(frozen=True, slots=True)
class LoanInventoryScope:
    """Exact provider/session/borrower scope. Empty primary borrower is valid."""
    identity_id: str
    provider_id: str
    credential_id: str
    account_id: str

    def __post_init__(self) -> None:
        if not all((
            _valid_token(self.identity_id),
            _valid_token(self.provider_id),
            _valid_token(self.credential_id),
            _valid_token(self.account_id, empty=True),
        )):
            raise ValueError("invalid_loan_inventory_scope")

    @classmethod
    def for_account(cls, account: Account) -> "LoanInventoryScope":
        if type(account) is not Account:
            raise ValueError("invalid_loan_inventory_account")
        return cls(account.identity_id, account.provider_id,
                   account.credential_id, account.unique_id)


@dataclass(frozen=True, slots=True)
class LoanBranchCoverage:
    """Provider-claimed coverage for one upstream and one medium.

    'complete' needs an explicitly observed end of pagination. This does not
    prove other branches or media were visited, and never grants rights writes.
    """
    upstream: str
    medium: Literal["digital", "physical", "unknown"]
    completeness: InventoryCompleteness = "unknown"
    pagination_complete: bool | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not (type(self.upstream) is str
                and 1 <= len(self.upstream) <= 48
                and self.upstream.isascii()
                and all(c.islower() or c.isdigit() or c in "_-" for c in self.upstream)
                and self.upstream[0].isalpha()):
            raise ValueError("invalid_loan_inventory_branch")
        if self.medium not in ("digital", "physical", "unknown"):
            raise ValueError("invalid_loan_inventory_medium")
        if self.completeness not in ("complete", "partial", "unknown"):
            raise ValueError("invalid_loan_inventory_coverage")
        if self.pagination_complete is not None and type(self.pagination_complete) is not bool:
            raise ValueError("invalid_loan_inventory_pagination")
        if self.completeness == "complete" and self.pagination_complete is not True:
            raise ValueError("unproven_loan_inventory_completeness")
        if self.reason_code is not None and self.reason_code not in _REASON_CODES:
            raise ValueError("invalid_loan_inventory_reason")


@dataclass(frozen=True, slots=True)
class LoanInventorySnapshot:
    """Atomic result envelope; rows are defensive copies of legacy Loan.

    The envelope, scope and coverages are frozen. The Loan model remains mutable
    for v1 compatibility; do not mutate its rows or treat completeness claims
    as transaction authority. Use positive rows only until Core adoption.
    """
    scope: LoanInventoryScope
    loans: tuple[Loan, ...] = field(repr=False)
    status: InventoryStatus = "partial"
    branches: tuple[LoanBranchCoverage, ...] = ()
    observed_at: str | None = None
    reason_code: str | None = None
    contract: str = INVENTORY_CONTRACT

    def __post_init__(self) -> None:
        if type(self.scope) is not LoanInventoryScope or self.contract != INVENTORY_CONTRACT:
            raise ValueError("invalid_loan_inventory_contract")
        if self.status not in ("success", "partial", "failed", "cancelled"):
            raise ValueError("invalid_loan_inventory_status")
        if self.reason_code is not None and self.reason_code not in _REASON_CODES:
            raise ValueError("invalid_loan_inventory_reason")
        if type(self.loans) is not tuple or len(self.loans) > _MAX_LOANS:
            raise ValueError("invalid_loan_inventory_rows")
        if type(self.branches) is not tuple or len(self.branches) > _MAX_BRANCHES:
            raise ValueError("invalid_loan_inventory_branches")
        if any(type(item) is not LoanBranchCoverage for item in self.branches):
            raise ValueError("invalid_loan_inventory_branches")
        branch_keys = {(b.upstream, b.medium) for b in self.branches}
        if len(branch_keys) != len(self.branches):
            raise ValueError("ambiguous_loan_inventory_branches")
        if self.observed_at is not None:
            if type(self.observed_at) is not str or not 20 <= len(self.observed_at) <= 40:
                raise ValueError("invalid_loan_inventory_timestamp")
            try:
                stamp = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
                if stamp.tzinfo is None or not 2000 <= stamp.year <= 2100:
                    raise ValueError
            except (ValueError, OverflowError):
                raise ValueError("invalid_loan_inventory_timestamp") from None
        for loan in self.loans:
            if not _loan_matches_scope(loan, self.scope):
                raise ValueError("mixed_loan_inventory_scope")
        # The provider must not be able to mutate rows *after* publishing its
        # result, including title, deadline or raw legacy metadata.
        object.__setattr__(self, "loans", tuple(deepcopy(loan) for loan in self.loans))

    def branch_claims_complete(self, upstream: str, medium: str) -> bool:
        """Informational claim only; never by itself a deletion/return permit."""
        return (
            self.status == "success"
            and any(
                row.upstream == upstream and row.medium == medium
                and row.completeness == "complete"
                and row.pagination_complete is True
                for row in self.branches
            )
        )


def _loan_matches_scope(loan: object, scope: LoanInventoryScope) -> bool:
    if type(loan) is not Loan:
        return False
    if not _valid_token(loan.id):
        return False
    if loan.medium not in ("digital", "physical", "unknown"):
        return False
    account = loan.source_account_id
    # Paris' legacy "primary" alias is accepted ONLY for its explicit
    # empty-ID main account, never as a wildcard for other borrowers.
    allowed_accounts = {scope.account_id}
    if scope.provider_id == "paris" and scope.account_id == "":
        allowed_accounts.add("primary")
    return (
        loan.identity_id == scope.identity_id
        and loan.source_provider == scope.provider_id
        and loan.source_credential == scope.credential_id
        and type(account) is str and account in allowed_accounts
    )


def legacy_loan_inventory(account: Account, rows: object) -> LoanInventorySnapshot:
    """Adapt a v1 list of positive Loan without inventing negative evidence."""
    scope = LoanInventoryScope.for_account(account)
    if type(rows) not in (list, tuple) or len(rows) > _MAX_LOANS:
        raise ValueError("invalid_legacy_loan_inventory")
    return LoanInventorySnapshot(
        scope=scope, loans=tuple(rows), status="partial",
        branches=(), reason_code="legacy_unverified",
    )


def read_loan_inventory(session: object, account: Account) -> LoanInventorySnapshot:
    """Explicit opt-in I/O read: no action, synchronization or mutation.

    New providers may implement loan_inventory_snapshot(account). All others
    use loans(account) with unknown coverage. Rejections never echo provider
    responses or exceptions. This API is intentionally not wired to rights
    reconciliation until #2975 / #3122 prove its consumer invariants.
    """
    scope = LoanInventoryScope.for_account(account)

    def still_bound() -> bool:
        # This guards in-place session/account changes. Core adoption must
        # additionally verify that this session is STILL the registered one.
        return (
            LoanInventoryScope.for_account(account) == scope
            and getattr(session, "connected", None) is True
            and getattr(session, "provider_id", None) == scope.provider_id
            and getattr(session, "credential_id", None) == scope.credential_id
            and getattr(getattr(session, "capabilities", None), "loans", None) is True
        )

    if not still_bound():
        return LoanInventorySnapshot(
            scope=scope, loans=(), status="failed", reason_code="unavailable",
        )
    reader = getattr(session, "loan_inventory_snapshot", None)
    try:
        if callable(reader):
            result = reader(account)
            if not still_bound():
                return LoanInventorySnapshot(
                    scope=scope, loans=(), status="failed", reason_code="scope_changed",
                )
            if type(result) is not LoanInventorySnapshot or result.scope != scope:
                raise ValueError("invalid_snapshot")
            # Revalidate every positive row even if the provider constructed
            # its own frozen envelope, then take a new defensive copy.
            return LoanInventorySnapshot(
                scope=scope, loans=result.loans, status=result.status,
                branches=result.branches, observed_at=result.observed_at,
                reason_code=result.reason_code,
            )
        rows = session.loans(account)
        if not still_bound():
            return LoanInventorySnapshot(
                scope=scope, loans=(), status="failed", reason_code="scope_changed",
            )
        return legacy_loan_inventory(account, rows)
    except Cancelled:
        # An explicit provider/task cancellation is not a network failure.
        # Keep the envelope empty: partially read positive rows are not an
        # atomic inventory and no negative entitlement proof is possible.
        return LoanInventorySnapshot(
            scope=scope, loans=(), status="cancelled", reason_code="cancelled",
        )
    except Exception:
        return LoanInventorySnapshot(
            scope=scope, loans=(), status="failed", reason_code="read_failed",
        )


__all__ = [
    "INVENTORY_CONTRACT", "LoanInventoryScope", "LoanBranchCoverage",
    "LoanInventorySnapshot", "legacy_loan_inventory", "read_loan_inventory",
]
