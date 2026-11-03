"""P0 #984: Adobe-first returns; only exact source identity and loan IDs.

No fuzzy matching, no UI identity, no ACSM token handling. Checkpoints are
hashed and deliberately contain no credential, profile, borrower or book ID.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .loan_return_guard import _public_timestamp, _verification_time_attested
from . import return_adept_origin_preflight


def adept_return_key(provider_id: str, credential_id: str,
                     account_id: str, loan_id: str) -> str:
    coordinates = (provider_id, credential_id, account_id, loan_id)
    if any(type(value) is not str or not value.strip() for value in coordinates):
        raise ValueError("exact Paris borrower and loan provenance required")
    parts = [provider_id.casefold(), credential_id, account_id, loan_id, "ADEPT"]
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return "loan.adept.return.v1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


_ADEPT_RETURN_STATES = frozenset({
    "request_sent", "adobe_return_requested", "adobe_return_confirmed",
    "paris_check_pending", "partial", "paris_return_confirmed", "failed",
})
_ADEPT_RETURN_VERIFICATIONS = frozenset({
    "not_checked", "adobe_pending", "adobe_confirmed", "adobe_unavailable",
    "still_present", "provider_absent", "unavailable",
})


_ADEPT_ATTESTED_PHASES = frozenset({
    "adobe_return_confirmed", "paris_check_pending",
    "partial", "paris_return_confirmed",
})


def adobe_return_attested(row: dict[str, Any]) -> bool:
    """A bare True flag on a pre-mutation checkpoint is not Adobe evidence."""
    status = row.get("status")
    return (
        type(status) is str
        and status in _ADEPT_ATTESTED_PHASES
        and row.get("adobe_verified") is True
    )


def paris_return_attested(row: dict[str, Any]) -> bool:
    """Trust final success only with Adobe proof and fresh Paris absence."""
    return (
        type(row.get("status")) is str
        and row["status"] == "paris_return_confirmed"
        and adobe_return_attested(row)
        and type(row.get("verification")) is str
        and row["verification"] == "provider_absent"
        and _verification_time_attested(row.get("checked_at"))
    )


def public_state(row: dict[str, Any]) -> dict[str, Any]:
    """Project only closed ADEPT/Paris checkpoint fields to the UI."""
    raw_status = row.get("status")
    status = (raw_status if type(raw_status) is str
              and raw_status in _ADEPT_RETURN_STATES else "failed")
    # A boolean flag on request_sent/adobe_return_requested is not evidence
    # that Adobe was called or independently verified after a crash.
    verified = adobe_return_attested(row)
    raw_verification = row.get("verification")
    verification = (raw_verification if type(raw_verification) is str
                    and raw_verification in _ADEPT_RETURN_VERIFICATIONS
                    else "not_checked")
    confirmed = paris_return_attested(row)
    if status == "paris_return_confirmed" and not confirmed:
        # A contradictory historical row must not show a completed return
        # or authorize an explicit provider action before a fresh read.
        status = "partial" if verified else "failed"
        verification = "not_checked"
    return {
        "status": status,
        "scope": "adobe_first_paris",
        "adobe_return_confirmed": verified,
        "provider_return_confirmed": confirmed,
        "paris_verification": verification if verification in {
            "still_present", "provider_absent", "unavailable"
        } else "not_checked",
        "requested_at": _public_timestamp(row.get("requested_at")),
        "checked_at": _public_timestamp(row.get("checked_at")),
        "retry": (
            "none" if confirmed else
            "provider_action_explicit" if verified and status == "partial"
            and verification == "still_present" else
            "verification_only"
        ),
    }


# These statuses originate from the Core's exact acquisition/borrower
# evidence. Do not stringify a foreign object or expose an arbitrary legacy
# value as a public status (it may contain a profile path or signed ACSM URL).
_ADEPT_ORIGIN_STATUSES = frozenset({
    "adept_identity_unproven", "adept_loan_identity_conflict",
    "adept_borrower_ambiguous", "adept_borrower_binding_required",
    "adept_borrower_connection_changed", "adept_borrower_profile_changed",
    "no_acquisition", "adept_return_acquisition_ambiguous",
    "adept_return_profile_conflict", "returned", "loan_id_missing",
    "profile_missing", "ready", "returned_unverified", "verification_failed",
    "expired", "unresolved", "ambiguous", "inventory_error",
    "binding_conflict", "token_missing", "provider_conflict",
})


def _public_origin_status(value: Any, *, default: str) -> str:
    return value if type(value) is str and value in _ADEPT_ORIGIN_STATUSES else default


def return_preflight_code(exc: BaseException) -> str:
    """Project only fixed Core borrower-refusal codes into public receipts."""
    try:
        code = getattr(exc, "code", None)
    except Exception:
        code = None
    if type(code) is str and code in {
        "loan_return_borrower_unproven", "loan_return_borrower_mismatch",
        "loan_return_source_unavailable",
    }:
        return code
    return "loan_return_borrower_unproven"


# Retired mutation helper: kept importable only to reject historical callers.
# Its non-mutating receipt/status projection remains available above.
def return_loan_prefer_adept(
    self, loan: Any, *, uuid, return_ref, adept_return_key, public_state,
    AttentionRequired, _TRACE_LOG,
) -> dict[str, Any]:
    """Reject the obsolete Adobe-first mutation before any provider access."""
    raise AttentionRequired(
        "Restitution volontaire retirée de Cifereca.",
        code="loan_return_removed",
    )
