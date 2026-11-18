"""Negative-only migration of historical ADEPT retry evidence (#3197).

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


def historical_adept_retry_blocked(storage, account, session, source: SourceKey) -> bool:
    if source.kind != "loan":
        return False
    try:
        scope = LoanInventoryScope.for_account(
            account, descriptor=getattr_static(session, "descriptor", None),
        )
        if (scope.identity_id, scope.provider_id, scope.credential_id,
                scope.account_id) != (
                source.scope.identity_id, source.scope.provider_id,
                source.scope.credential_id, source.scope.account_id):
            return True
        # Empty primary UID and descriptor-attested alias may denote the
        # same historical receipt. Never infer the alias from a supplier name
        # or convert a missing account to a linked reader.
        borrowers = tuple(dict.fromkeys(
            (scope.account_id,) if scope.primary_account_alias is None else
            (scope.account_id, scope.primary_account_alias)
        ))
        placeholders = ",".join("?" for _ in borrowers)
        with storage._lock, storage._connect() as db:
            rows = db.execute(
                "SELECT entitlement_id,metadata_json FROM acquisition_ledger "
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
            try:
                metadata = json.loads(raw) if raw is not None else None
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
    except Exception:
        # Unreadable evidence cannot license another potentially one-use GET.
        return True
