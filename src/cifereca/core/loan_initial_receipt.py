"""Pure initial digital-loan ledger payloads extracted from LibraryService.

P0 #1120 — the owner, borrower, loan and action decision are resolved by
the calling service. This module is NOT an acquisition permission and never
performs storage writes, provider calls, ADEPT operations or retries.
"""
from __future__ import annotations

from typing import Any


def ledger_base(loan: Any, *, entitlement_id: Any, identity_id: Any) -> dict:
    """Preserve the original exact transaction/owner mapping and key order."""
    return {
        "entitlement_id": entitlement_id,
        "provider_id": loan.source_provider,
        "credential_id": loan.source_credential,
        "account_id": loan.source_account_id,
        "identity_id": identity_id,
        "item_id": str(loan.record_id or loan.id),
        "title": loan.title,
    }


def acquiring_receipt(base: dict, loan: Any, previous_meta: dict) -> dict:
    """The pre-provider 'acquiring' receipt, unchanged and side-effect-free."""
    return {
        **base, "status": "acquiring", "strategy": "loan_auto",
        "executor": "provider",
        "metadata": {**previous_meta, "provider_loan_id": loan.id},
    }
