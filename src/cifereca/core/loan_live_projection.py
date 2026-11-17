"""Fail-closed loan display admission against the current GTK action inventory.

Service.last_loans can change independently of the identity-scoped loan_objects
map (async refresh, profile switch, cache replay). A list row must never
display or register N2 evidence for a DTO that its action map does not own.
No network, provider, DRM, GTK or filesystem work.
"""
from __future__ import annotations

from collections.abc import Mapping, Iterable

from ..models import Loan
from .loan_source_identity import loan_source_key, unambiguous_loan_rows


def current_actionable_loan_rows(
    rows: Iterable[object], objects: Mapping[str, object], *,
    identity_id: str | None,
) -> tuple[Loan, ...]:
    """Only exact live DTO instances already owned by the active action map."""
    if (type(identity_id) is not str or not identity_id
            or type(objects) is not dict or len(objects) > 10000):
        return ()
    try:
        candidates = unambiguous_loan_rows(rows)
        result = []
        for loan in candidates:
            if (loan.identity_id == identity_id
                    and objects.get(loan_source_key(loan)) is loan):
                result.append(loan)
        return tuple(result)
    except Exception:
        return ()


__all__ = ["current_actionable_loan_rows"]
