"""Compatibility boundary for retired provider digital-loan return (#4048).

Cifereca never issues a voluntary provider return request. Existing callers
receive a closed non-mutating result. Historical SQLite state is untouched.
"""
from __future__ import annotations

from typing import Any

from ..models import Loan


def return_digital(
    self, loan: Loan, *, explicit_retry: bool = False, _TRACE_LOG,
) -> dict[str, Any]:
    """Retired return path; no session, SQLite, network or ADEPT access."""
    return {"status": "blocked", "code": "loan_return_removed", "retry": "none"}
