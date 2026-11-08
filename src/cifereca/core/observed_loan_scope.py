"""Inert coordinates for a loan seen during a manual file import.

These coordinates provide an exact display join ONLY. The observation
does not prove loan validity, file ownership, ADEPT authority, or purchase.
Consumers must verify current rights, source and file integrity separately.
"""
from __future__ import annotations

FIELDS = ("identity_id", "credential_id", "account_id", "loan_id", "item_id")


def observed_loan(loan):
    """Capture an upstream observation as display coordinates, without I/O."""
    return dict(zip(
        FIELDS,
        (loan.identity_id, loan.source_credential, loan.source_account_id,
         loan.id, loan.record_id),
    ))


def scope_key(value):
    """No trimming, coercion or guessing missing borrower/account fields."""
    if (type(value) is not dict or set(value) != set(FIELDS)
            or any(type(value.get(k)) is not str or not value[k] for k in FIELDS)):
        return None
    return tuple(value[k] for k in FIELDS)
