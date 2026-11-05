"""Retired provider return entry point; direct callers fail closed (#4048)."""
from __future__ import annotations

from typing import Any

from ..models import Loan
from ..plugin_api import AttentionRequired


def return_digital(self, loan: Loan, *, explicit_retry: bool = False, _TRACE_LOG) -> dict[str, Any]:
    """Reject old provider returns before a lock, checkpoint or provider call."""
    raise AttentionRequired(
        "Restitution volontaire retirée de Cifereca.",
        code="loan_return_removed",
    )
