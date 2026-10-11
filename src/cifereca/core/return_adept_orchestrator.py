"""P0 #984: Adobe-first returns; only exact source identity and loan IDs.

No fuzzy matching, no UI identity, no ACSM token handling. Checkpoints are
hashed and deliberately contain no credential, profile, borrower or book ID.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def adept_return_key(provider_id: str, credential_id: str,
                     account_id: str, loan_id: str) -> str:
    parts = [provider_id.casefold(), credential_id, account_id, loan_id, "ADEPT"]
    if not all(str(part or "").strip() for part in parts):
        raise ValueError("exact Paris borrower and loan provenance required")
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return "loan.adept.return.v1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def public_state(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "failed")
    verified = row.get("adobe_verified") is True
    verification = str(row.get("verification") or "not_checked")
    return {
        "status": status,
        "scope": "adobe_first_paris",
        "adobe_return_confirmed": verified,
        "provider_return_confirmed": status == "paris_return_confirmed",
        "paris_verification": verification if verification in {
            "still_present", "provider_absent", "unavailable"
        } else "not_checked",
        "requested_at": row.get("requested_at"),
        "checked_at": row.get("checked_at"),
        "retry": (
            "none" if status == "paris_return_confirmed" else
            "provider_action_explicit" if verified and status == "partial"
            and verification == "still_present" else
            "verification_only"
        ),
    }
