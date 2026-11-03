"""Guarded provider-only loan return behind LibraryService's existing lock.

Keep the durable SQLite claim, Adobe checkpoint precedence, uncertain POST
handling and fresh verification/retry order. No Adobe mutation or acquisition
is added. The facade injects its logger; UUID and public guards remain late
imports at their historical execution points.

P0 #1120 structural closure: see
docs/audits/2026-10-21-p0-1120-structural-closure.md for the audited
at-most-once/retry boundary and its explicitly unverified real-provider cases.
"""
from ..plugin_api import AttentionRequired
from __future__ import annotations
from typing import Any
from ..models import Loan


def return_digital(self, loan: Loan, *, explicit_retry: bool = False, _TRACE_LOG) -> dict[str, Any]:
    """Retired provider return cannot acquire a claim or contact any provider."""
    raise AttentionRequired(
        "Restitution volontaire retirée de Cifereca.",
        code="loan_return_removed",
    )
