"""In-memory, non-authorizing loan observations for one BnF reader epoch.

This is not a rights ledger, server inventory, or Core synchronization.
A partial/empty/failed read must never imply a returned or expired loan.
No persistence, HTML, URLs, browser state, account labels or secrets.
"""
from __future__ import annotations

from dataclasses import dataclass

from cifereca_bnf_pret_web import (
    LoanRecord, LoanSnapshot, ProfileScope, WebParseError,
    MAX_ITEMS, _id, merge_observed_loans,
)


@dataclass(frozen=True)
class ObservedLoanCounts:
    """Safe counters; historical count is *ever seen*, not currently held."""
    on_last_page: int
    newly_seen: int
    seen_in_session: int
    status: str = "partial"


class ObservedLoans:
    """Accumulate observations within one immutable identity/account/epoch scope.

    Pages are not complete inventories. The only information exposed by the
    public projection is fixed counters, never resource/loan identifiers.
    """

    def __init__(self, scope: ProfileScope):
        if not isinstance(scope, ProfileScope):
            raise WebParseError("invalid_scope")
        self._scope = scope
        self._merged: LoanSnapshot | None = None
        self._closed = False

    def __repr__(self):
        return f"ObservedLoans(state={'closed' if self._closed else 'active'!r})"

    def close(self):
        """Invalidate all observations after identity change, logout or UI close."""
        self._merged = None
        self._closed = True

    def observe(self, snapshot: LoanSnapshot) -> ObservedLoanCounts:
        if self._closed:
            raise WebParseError("session_closed")
        if not isinstance(snapshot, LoanSnapshot) or snapshot.scope != self._scope:
            raise WebParseError("scope_mismatch")
        if snapshot.exhaustive is not False or snapshot.status not in {"partial", "observed"}:
            raise WebParseError("invalid_observation")
        if len(snapshot.loans) > MAX_ITEMS:
            raise WebParseError("item_limit")
        current: dict[str, LoanRecord] = {}
        for loan in snapshot.loans:
            if not isinstance(loan, LoanRecord):
                raise WebParseError("invalid_observation")
            if (_id("/loans/" + loan.loan_id, "loans") != loan.loan_id
                    or _id("/resources/" + loan.resource_id, "resources") != loan.resource_id):
                raise WebParseError("invalid_observation")
            if loan.loan_id in current and current[loan.loan_id] != loan:
                raise WebParseError("ambiguous_loans")
            current[loan.loan_id] = loan
        old = {loan.loan_id for loan in self._merged.loans} if self._merged else set()
        candidate = (merge_observed_loans(self._merged, snapshot)
                     if self._merged else snapshot)
        # Commit only after every validation/merge succeeds.
        self._merged = candidate
        return ObservedLoanCounts(
            on_last_page=len(current),
            newly_seen=len(set(current) - old),
            seen_in_session=len(candidate.loans),
        )
