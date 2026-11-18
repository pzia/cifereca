"""Borrower admission for Core-selected loan deliveries (#3197).

The SourceKey is authenticated A, not Adobe permission. Reuse the existing
source-scoped v16 grant resolver (v15 may only veto), cross-identity conflict
guard and durable connection epoch. No provider I/O or ledger mutation.
"""
from __future__ import annotations

from ..models import Loan
from .adept_borrower import public_borrower_refusal_code, _cross_identity_loan_conflict
from .loan_source_epoch import SourceEpochLease


def pin_borrower(service, source):
    scope = source.scope
    loan = Loan(
        id=source.right_id, record_id=source.edition_id,
        provider="UNKNOWN", medium="digital", title="",
        source_provider=scope.provider_id,
        source_credential=scope.credential_id,
        source_account_id=scope.account_id,
        identity_id=scope.identity_id,
    )
    storage = service.storage
    lease = SourceEpochLease.capture(storage, loan, scope.identity_id)
    if lease is None:
        raise ValueError("canonical_delivery_borrower_source_changed")

    def resolve():
        if not lease.current(storage, loan):
            raise ValueError("canonical_delivery_borrower_source_changed")
        # Use the neutral v16 grant boundary, not the historical facade.
        try:
            owner, reason = service.resolve_adept_borrower_for_loan(
                loan, connection_identity_id=scope.identity_id,
            )
        except Exception:
            raise ValueError("adept_borrower_ambiguous") from None
        if not lease.current(storage, loan):
            raise ValueError("canonical_delivery_borrower_source_changed")
        if reason is not None or type(owner) is not str or not owner:
            raise ValueError(public_borrower_refusal_code(reason))
        if _cross_identity_loan_conflict(
            storage.acquisitions(provider_id=scope.provider_id),
            provider_loan_id=source.right_id, identity_id=scope.identity_id,
            entitlements=storage.entitlements(
                provider_id=scope.provider_id, state="active",
            ),
        ):
            raise ValueError("adept_loan_identity_conflict")
        if not lease.current(storage, loan):
            raise ValueError("canonical_delivery_borrower_source_changed")
        return owner

    borrower = resolve()

    def current():
        return service.storage is storage and resolve() == borrower

    return borrower, current
