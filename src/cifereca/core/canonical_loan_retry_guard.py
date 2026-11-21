"""Negative-only historical and canonical ADEPT retry evidence (#3197).

No legacy receipt ever authorizes a delivery, proves B, creates a grant or
selects a profile. The exact loan's outstanding explicit-only attention
survives switching from the historical opening writer to canonical Sync.
"""
from __future__ import annotations

import json
from inspect import getattr_static

from ..plugin_api.loan_inventory import LoanInventoryScope
from .adept_retry_guard import blocked_automatic_adept_retry
from .source_convergence import SourceKey
from .canonical_adept_attempt import pending_loan_adept_attempt


def historical_adept_retry_blocked(storage, account, session, source: SourceKey) -> bool:
    if source.kind != "loan":
        return False
    if pending_loan_adept_attempt(storage, source):
        return True
    try:
        scope = LoanInventoryScope.for_account(
            account, descriptor=getattr_static(session, "descriptor", None),
        )
        if (scope.identity_id, scope.provider_id, scope.credential_id,
                scope.account_id) != (
                source.scope.identity_id, source.scope.provider_id,
                source.scope.credential_id, source.scope.account_id):
            return True
        with storage._lock:
            db = storage._connect()
            if db.in_transaction:
                return True
            return historical_adept_ledger_hold(db, scope, source)
    except Exception:
        # Unreadable evidence cannot license another potentially one-use GET.
        return True


def historical_adept_ledger_hold(db, scope: LoanInventoryScope, source: SourceKey) -> bool:
    """Read the same legacy negative evidence in an existing SQLite snapshot."""
    if (scope.identity_id, scope.provider_id, scope.credential_id, scope.account_id) != (
            source.scope.identity_id, source.scope.provider_id,
            source.scope.credential_id, source.scope.account_id):
        return True
    borrowers = tuple(dict.fromkeys(
        (scope.account_id,) if scope.primary_account_alias is None else
        (scope.account_id, scope.primary_account_alias)
    ))
    placeholders = ",".join("?" for _ in borrowers)
    rows = db.execute(
        "SELECT entitlement_id,CASE WHEN length(CAST(metadata_json AS BLOB))<=65536 "
        "THEN metadata_json ELSE NULL END FROM acquisition_ledger "
        "WHERE identity_id=? AND provider_id=? AND credential_id=? "
        "AND (account_id IN (" + placeholders + ") "
        "OR (account_id IS NULL AND ?='')) AND item_id=? "
        "AND status='attention_required' AND strategy='loan_auto' "
        "AND executor='kobo-adept' LIMIT 257",
        (scope.identity_id, scope.provider_id, scope.credential_id,
         *borrowers, scope.account_id, source.edition_id),
    ).fetchall()
    if len(rows) > 256:
        return True
    for entitlement_id, raw in rows:
        # An oversized/non-text or missing ledger payload is unknown,
        # not proof that this historical attempt belongs to another loan.
        if type(raw) is not str:
            return True
        try:
            metadata = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            return True
        if blocked_automatic_adept_retry(
            {"status": "attention_required", "strategy": "loan_auto",
             "executor": "kobo-adept", "metadata": metadata,
             "entitlement_id": entitlement_id},
            provider_loan_id=source.right_id,
            entitlement_id=None, force_refresh=False,
        ):
            return True
    return False
