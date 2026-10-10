"""P0 #984: strict acquisition provenance for Adobe-first Paris returns.

No matching by book title, ISBN alone, current UI identity or other Paris
credentials.  The Adobe profile/loan IDs come only from the original
acquisition's recorded ADEPT binding.  There is no DRM implementation here.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def adept_return_key(provider_id: str, credential_id: str,
                     borrower_id: str, loan_id: str) -> str:
    raw = json.dumps(
        [provider_id, credential_id, borrower_id, loan_id, "ADEPT"],
        ensure_ascii=False, separators=(",", ":"),
    )
    return "loan.adept.return.v1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def acquisition_for_loan(rows: list[dict[str, Any]], *, provider_id: str,
                         credential_id: str, borrower_id: str, loan_id: str,
                         item_id: str, identity_id: str | None) -> tuple[str, dict[str, Any] | None]:
    """Resolve an exact fulfilled ADEPT acquisition or an explicit stop reason."""
    matches = []
    for row in rows:
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        if (row.get("provider_id") != provider_id
                or row.get("credential_id") != credential_id
                or str(row.get("account_id") or "primary") != borrower_id
                or str(row.get("item_id") or "") != item_id
                or str(metadata.get("provider_loan_id") or "") != loan_id
                or row.get("strategy") != "loan_auto"):
            continue
        matches.append(row)
    if not matches:
        return "provider_action_available", None
    if len(matches) != 1:
        return "adept_return_ambiguous", None
    row = matches[0]
    if (row.get("executor") != "kobo-adept"
            or row.get("status") != "complete"):
        return "provider_action_available", None
    binding = (row.get("metadata") or {}).get("adept_binding") or {}
    if (not isinstance(binding, dict)
            or not isinstance(binding.get("adept_loan_id"), str)
            or not binding["adept_loan_id"]
            or not isinstance(binding.get("adept_profile_id"), str)
            or not binding["adept_profile_id"]):
        return "adept_return_binding_required", None
    # User-selected UI identity is never sufficient evidence. The source loan
    # and canonical acquisition must have independently matching identities.
    if not identity_id or row.get("identity_id") != identity_id:
        return "adept_return_identity_unproven", None
    return "adobe_bound", row


def public_state(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "failed")
    verified = row.get("adobe_verified") is True
    return {
        "status": status,
        "scope": "adobe_first_paris",
        "adobe_return_confirmed": verified,
        "provider_return_confirmed": status == "paris_return_confirmed",
        "paris_verification": str(row.get("verification") or "not_checked"),
        "requested_at": row.get("requested_at"),
        "checked_at": row.get("checked_at"),
        "retry": (
            "none" if status == "paris_return_confirmed" else
            "provider_action_explicit" if verified and status == "partial"
            and row.get("verification") == "still_present" else
            "verification_only"
        ),
    }


__all__ = ["adept_return_key", "acquisition_for_loan", "public_state"]
