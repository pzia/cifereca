"""Pure ADEPT profile-preflight attention receipt projection (#1120).

The caller owns borrower/profile resolution, the one ledger write and the
locked ACSM/ADEPT workflow. No provider, profile or filesystem access here.
Keep the historical dictionary key order and preexisting metadata unchanged.
"""
from __future__ import annotations

from typing import Any, Mapping


def attention_receipt(
    *, ledger_base: Mapping[str, Any], previous_meta: Mapping[str, Any],
    loan_id: Any, retained_acsm: Any, code: str,
    error_message: str | None = None,
    attempt_profile: Any = None,
    include_attempt_profile: bool = False,
) -> dict[str, Any]:
    """Build exactly the three existing profile-preflight refusal payloads."""
    metadata = {
        **previous_meta, "provider_loan_id": loan_id,
        "acsm_staging_path": str(retained_acsm),
    }
    if include_attempt_profile:
        metadata["adept_attempt_profile"] = attempt_profile
    receipt = {
        **ledger_base, "status": "attention_required",
        "strategy": "loan_auto", "executor": "kobo-adept",
        "attention_code": code,
    }
    if error_message is not None:
        receipt["error_message"] = error_message
    receipt["metadata"] = metadata
    return receipt
