"""Headless-safe exact loan -> Core EPUB proof gateway (P1 #6168).

The live GTK host and headless loan controller adapters share this pure
function; no GTK, provider access, SQLite or physical image read is required.
"""
from __future__ import annotations

from typing import Any

from ..models import Loan
from ..core.loan_cover_evidence import exact_attested_loan_epub_cover


def loan_embedded_cover_for_host(host: Any, loan: Loan):
    """Accept only the active borrower's current materialized Library epoch."""
    active = getattr(
        getattr(getattr(host, "service", None), "current_identity", None),
        "identity_id", None,
    )
    projection = getattr(host, "_loan_cover_projection", None)
    if (type(loan) is not Loan or type(active) is not str or not active
            or loan.identity_id != active
            or type(projection) is not tuple or len(projection) != 3
            or projection[0] != getattr(
                getattr(host, "service", None), "_account_context_epoch", None,
            )
            or projection[1] != active
            or type(projection[2]) is not tuple or not projection[2]):
        return None
    return exact_attested_loan_epub_cover(loan, projection[2])


__all__ = ["loan_embedded_cover_for_host"]
