"""Synthetic complete DILICOM inventory, explicitly opt-in for return tests.

A v1 loan list is never itself evidence of complete pagination.
"""
from __future__ import annotations

from types import SimpleNamespace

from cifereca.plugin_api.loan_inventory import (
    LoanBranchCoverage, LoanInventoryScope, LoanInventorySnapshot,
)


def attest_paris_dilicom_complete(session, *, credential_id: str) -> None:
    """Simulate a branch-scoped end of pagination; no real provider I/O."""
    session.provider_id = "paris"
    session.credential_id = credential_id
    session.capabilities = SimpleNamespace(loans=True)

    def loan_inventory_snapshot(account):
        rows = session.loans(account)
        return LoanInventorySnapshot(
            scope=LoanInventoryScope.for_account(account),
            loans=tuple(rows), status="success",
            branches=(LoanBranchCoverage(
                upstream="dilicom", medium="digital", completeness="complete",
                pagination_complete=True,
            ),),
        )

    session.loan_inventory_snapshot = loan_inventory_snapshot
