"""P0 #982: stable, redacted identity for a provider loan-return attempt.

ReturnDigitalLoan acceptance is not evidence that a PNB loan is absent.
Persist only operation metadata: no loan IDs, user credentials, ACSM URLs,
borrower names or upstream response bodies.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone


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


# Checkpoints can predate the current version or be corrupt. Never let
# metadata read from disk become free text in GTK or diagnostic exports.
_RETURN_STATUSES = frozenset({
    "request_sent", "pending_verification", "verification_unavailable",
    "still_active", "provider_return_confirmed",
})
_RETURN_VERIFICATIONS = frozenset({
    "not_checked", "unavailable", "still_present", "provider_absent",
})
_ISO_UTC = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


def _public_timestamp(value) -> str | None:
    """Preserve only plausible timezone-aware timestamps; never stringify."""
    if type(value) is not str or len(value) > 40 or _ISO_UTC.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if 2000 <= parsed.year <= 2100 and parsed.utcoffset() is not None else None


def _verification_time_attested(value, *, now: datetime | None = None) -> bool:
    """A fabricated future clock cannot attest a completed inventory read.

    Reuse the validated public ISO timestamp policy. Allow up to three minutes
    of ordinary clock skew; older confirmed returns remain terminal (no TTL).
    This validation performs no network I/O and never coerces raw values.
    """
    stamp = _public_timestamp(value)
    if stamp is None:
        return False
    checked = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    clock = datetime.now(timezone.utc) if now is None else now
    return checked <= clock + timedelta(seconds=180)


def provider_absence_attested(row: dict) -> bool:
    """A durable status alone is not proof of a fresh digital-loan inventory.

    Request acceptance does not imply success; a crash-recovered POST may be
    confirmed by absence even when the HTTP acknowledgement was never saved.
    Refuse contradictory or corrupted checkpoints without coercing any field.
    """
    return (
        type(row.get("status")) is str
        and row["status"] == "provider_return_confirmed"
        and type(row.get("verification")) is str
        and row["verification"] == "provider_absent"
        and _verification_time_attested(row.get("checked_at"))
    )


def public_return_state(row: dict) -> dict:
    raw_status = row.get("status")
    status = (
        raw_status if type(raw_status) is str and raw_status in _RETURN_STATUSES
        else "pending_verification" if (
            raw_status is None or type(raw_status) is str and raw_status == ""
        ) else "verification_unavailable"
    )
    raw_verification = row.get("verification")
    verification = (
        raw_verification if type(raw_verification) is str
        and raw_verification in _RETURN_VERIFICATIONS
        else "not_checked" if (
            raw_verification is None or type(raw_verification) is str
            and raw_verification == ""
        ) else "unavailable"
    )
    if status == "provider_return_confirmed" and not provider_absence_attested(row):
        # A mismatched verification code or missing checked_at is not proof.
        # Keep the original ledger byte-for-byte; only the read projection
        # becomes verification-required instead of declaring a loan returned.
        status, verification = "verification_unavailable", "unavailable"
    return {
        "status": status,
        "request_accepted": row.get("request_accepted") is True,
        "provider_inventory_verified": status == "provider_return_confirmed",
        "verification": verification,
        "checked_at": _public_timestamp(row.get("checked_at")),
        "requested_at": _public_timestamp(row.get("requested_at")),
        "retry": "verification_first" if status != "provider_return_confirmed" else "none",
        "scope": "provider_pnb_only",
    }
