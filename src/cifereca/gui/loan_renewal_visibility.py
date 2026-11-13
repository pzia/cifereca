"""Read-only, non-authorizing physical-loan renewal display policy.

The Core always re-reads the exact loan and applies its own mutation guards.
"""
from __future__ import annotations

from ..models import Loan


def paris_physical_renewal_visible(loan: object) -> bool:
    """Display a physical loan only when its normalized source says renewable."""
    return (
        type(loan) is Loan
        and loan.medium == "physical"
        and loan.renewable is True
        and (loan.state is None or (
            type(loan.state) is str
            and loan.state.casefold() not in {
                "returned", "closed", "expired", "cancelled", "canceled",
            }
        ))
    )
