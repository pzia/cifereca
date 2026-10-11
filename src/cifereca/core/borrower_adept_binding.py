"""Borrower-scoped Adobe identity proof; never infer a DRM identity from UI state.

A loan may appear through more than one Paris connection. A confirmed borrower
binding is scoped to the exact credential and upstream borrower account; a
shared-loan owner decision is scoped to the provider's exact loan transaction
and item, never a title/ISBN. Both live in existing local SQLite metadata.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def borrower_key(provider_id: str, credential_id: str, account_id: str) -> str:
    key = json.dumps([provider_id.casefold(), credential_id, account_id],
                     ensure_ascii=False, separators=(",", ":"))
    return "adept.borrower.v1:" + hashlib.sha256(key.encode("utf-8")).hexdigest()


def loan_owner_key(provider_id: str, loan_id: str, item_id: str) -> str:
    key = json.dumps([provider_id.casefold(), loan_id, item_id],
                     ensure_ascii=False, separators=(",", ":"))
    return "adept.loan.owner.v1:" + hashlib.sha256(key.encode("utf-8")).hexdigest()


def read_proof(storage: Any, key: str) -> dict[str, str] | None:
    raw = storage.metadata_get(key)
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        return None
    try:
        row = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(row, dict) or row.get("schema") != 1 or row.get("confirmed") is not True:
        return None
    if not all(isinstance(row.get(x), str) and row[x] for x in ("identity_id", "profile_id")):
        return None
    return row


def save_proof(storage: Any, key: str, *, identity_id: str, profile_id: str,
               owner_borrower_key: str | None = None) -> None:
    data = {"schema": 1, "confirmed": True,
            "identity_id": identity_id, "profile_id": profile_id}
    if owner_borrower_key:
        data["owner_borrower_key"] = owner_borrower_key
    storage.metadata_set(key, json.dumps(data, sort_keys=True, separators=(",", ":")))


def conflicting_loan_identities(
    entitlements: list[dict[str, Any]],
    acquisitions: list[dict[str, Any]],
    *, provider_id: str, loan_id: str, item_id: str,
) -> set[str]:
    """Use exact upstream loan id + item id, not title/ISBN heuristics."""
    identities = {
        str(r.get("identity_id") or "")
        for r in entitlements
        if r.get("provider_id") == provider_id and str(r.get("loan_id") or "") == loan_id
        and str(r.get("item_id") or "") == item_id and r.get("identity_id")
    }
    identities.update(
        str(row.get("identity_id") or "")
        for row in acquisitions
        if row.get("provider_id") == provider_id
        and str(row.get("item_id") or "") == item_id
        and str((row.get("metadata") or {}).get("provider_loan_id") or "") == loan_id
        and row.get("identity_id")
    )
    return identities


__all__ = [
    "borrower_key", "loan_owner_key", "read_proof", "save_proof",
    "conflicting_loan_identities",
]
