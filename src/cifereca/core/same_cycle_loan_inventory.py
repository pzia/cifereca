"""P1 #4281 — ephemeral Core-only lease for one already validated loan read.

The lease exists solely inside ONE opening-sync account's canonical observation.
It is not an inventory cache, authority over loan rights, or a permission to
reuse data after an account/session switch. It never persists or crosses tasks.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from ..models import Account
from ..plugin_api.loan_inventory import LoanInventoryScope, LoanInventorySnapshot


_CURRENT: ContextVar[tuple[Any, Account, Any, LoanInventorySnapshot, object] | None] = ContextVar(
    "cifereca_opening_scoped_loan_inventory", default=None,
)


@contextmanager
def opening_scoped_loan_inventory(service, account, session, snapshot):
    """Offer exactly one frozen typed inventory during the next rights phase.

    Invalid or partial inventories are never offered. The rights reader still
    performs its full Core registry, enabled-source and persistent-owner gates;
    providers must separately opt in to interpreting this evidence.
    """
    allowed = (
        type(account) is Account
        and type(snapshot) is LoanInventorySnapshot
        and snapshot.scope == LoanInventoryScope.for_account(account)
        and snapshot.status == "success"
        and bool(snapshot.branches)
        and all(
            branch.completeness == "complete"
            and branch.pagination_complete is True
            for branch in snapshot.branches
        )
        and session is not None
        and getattr(session, "connected", None) is True
    ) if type(account) is Account and type(snapshot) is LoanInventorySnapshot else False
    if not allowed:
        yield
        return
    epoch = getattr(session, "_epoch", None)
    token = _CURRENT.set((service, account, session, snapshot, epoch))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_scoped_loan_inventory(service, account, session) -> LoanInventorySnapshot | None:
    """Read the one scoped lease; never select by provider name or credential."""
    current = _CURRENT.get()
    if current is None:
        return None
    owner, borrower, bound_session, snapshot, epoch = current
    if (owner is not service or borrower is not account or bound_session is not session
            or getattr(session, "connected", None) is not True
            or getattr(session, "_epoch", None) != epoch
            or type(snapshot) is not LoanInventorySnapshot
            or snapshot.status != "success"):
        return None
    try:
        if snapshot.scope != LoanInventoryScope.for_account(account):
            return None
    except (TypeError, ValueError, AttributeError):
        return None
    return snapshot


__all__ = ["opening_scoped_loan_inventory", "current_scoped_loan_inventory"]
