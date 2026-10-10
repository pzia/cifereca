"""P0 #982: stable, redacted identity for a provider loan-return attempt.

ReturnDigitalLoan acceptance is not evidence that a PNB loan is absent.
Persist only operation metadata: no loan IDs, user credentials, ACSM URLs,
borrower names or upstream response bodies.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


def return_key(provider_id: str, credential_id: str, borrower_id: str,
               provider_loan_id: str) -> str:
    parts = [provider_id.casefold(), credential_id, borrower_id, provider_loan_id]
    if any(not str(part) for part in parts):
        raise ValueError("loan return needs exact provider, credential, borrower and loan ID")
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return "loan.return.v1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def return_ref(key: str) -> str:
    return key.rsplit(":", 1)[-1][:12]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def public_return_state(row: dict) -> dict:
    status = str(row.get("status") or "pending_verification")
    return {
        "status": status,
        "request_accepted": bool(row.get("request_accepted", False)),
        "provider_inventory_verified": status == "provider_return_confirmed",
        "verification": str(row.get("verification") or "not_checked"),
        "checked_at": row.get("checked_at"),
        "requested_at": row.get("requested_at"),
        "retry": "verification_first" if status != "provider_return_confirmed" else "none",
        "scope": "provider_pnb_only",
    }
