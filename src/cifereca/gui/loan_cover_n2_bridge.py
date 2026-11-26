"""Headless-safe exact loan -> Core EPUB proof gateway (P1 #6168).

The live GTK host and headless loan controller adapters share this pure
function; no GTK, provider access, SQLite or physical image read is required.
"""
from __future__ import annotations

from typing import Any, Callable
from copy import copy

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
    from .loan_cover_decision_bridge import current_loan_embedded_conflict
    if current_loan_embedded_conflict(host, loan):
        return None
    return exact_attested_loan_epub_cover(loan, projection[2])


def show_attested_loan_cover(
    host: Any, loan: Loan, display: Callable[[], Loan],
) -> bool:
    """Render through N2 only if the exact borrower/Placement proof exists.

    False means the unchanged legacy DetailPanel path should render the loan.
    The host supplies its existing display-only Loan copy; this gateway never
    calls a provider or mutates the canonical loan DTO.
    """
    evidence = loan_embedded_cover_for_host(host, loan)
    if evidence is None:
        return False
    panel = getattr(getattr(host, "loan_page", None), "detail", None)
    presentation = display()
    if callable(getattr(panel, "show_loan_local", None)):
        panel.show_loan_local(presentation, cover_evidence=evidence)
    else:
        # An old/custom host cannot paint weaker URL evidence ahead of the
        # exact EPUB: keep the loan content but suppress its URL cover.
        neutral = copy(presentation)
        neutral.thumbnail_url = None
        panel.show_loan(neutral)
    return True


__all__ = ["loan_embedded_cover_for_host", "show_attested_loan_cover"]
