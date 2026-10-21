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


# LibraryService retains the shared loan-operation lock. Dependencies and the
# historical public guard helpers are imported/injected by its facade at call
# time; the mutation/checkpoint/verification body below keeps its original order.
def return_loan_prefer_adept(
    self, loan: Any, *, uuid, return_ref, adept_return_key, public_state,
    AttentionRequired, _TRACE_LOG,
) -> dict[str, Any]:
    """Adobe-first, then Paris inventory; never automatically send PNB POST.

    Durable claim is written *before* ADEPT. A retry, double-click or
    restart only reads ADEPT/Paris inventories: Adobe is never re-mutated.
    """

    if str(loan.source_provider).lower() != "paris" or loan.medium != "digital":
        return self.return_digital(loan)
    try:
        account, _session, pnb_key = self._return_loan_identity(loan)
    except AttentionRequired as exc:
        code = return_preflight_code(exc)
        return {
            "status": "failed", "scope": "adobe_first_paris",
            "code": code, "retry": "resolve_identity",
        }
    borrower = str(account.unique_id or "primary")
    key = adept_return_key(
        loan.source_provider, loan.source_credential, borrower, str(loan.id)
    )
    source = self.adept_return_status_for_loan(loan)
    if type(source) is not dict:
        source = {}
    raw_id = source.get("acquisition_id")
    acquisition_id = (
        raw_id if type(raw_id) is str and 0 < len(raw_id) <= 256 else None
    )
    if acquisition_id is None:
        return return_adept_origin_preflight.missing_acquisition_outcome(
            source, public_origin_status=_public_origin_status,
        )
    acquisition = self.storage.acquisition_get(acquisition_id)
    if acquisition is None:
        return {
            "status": "adept_return_binding_required",
            "scope": "adobe_first_paris", "retry": "resolve_identity",
        }

    # A previous PNB mutation must be settled separately; never send both.
    if self.storage.loan_return_state(pnb_key) is not None:
        return {
            "status": "partial", "scope": "adobe_first_paris",
            "reason": "provider_return_already_requested",
            "retry": "verification_only",
        }
    previous = self.storage.loan_return_state(key)
    if previous is None:
        # Pure preflight decision precedes the *first* durable return claim.
        refusal = return_adept_origin_preflight.unready_to_return_outcome(
            source, public_origin_status=_public_origin_status,
        )
        if refusal is not None:
            return refusal
        claimed, record = self.storage.loan_return_claim(key, uuid.uuid4().hex)
        if not claimed:
            previous = record
    if previous is not None:
        if paris_return_attested(previous):
            return public_state(previous)
        op_id = str(previous.get("operation_id") or "")
        if not adobe_return_attested(previous):
            if not self._adobe_return_inventory_confirmed(acquisition):
                return public_state(previous)
            record = self.storage.loan_return_transition(
                key, op_id, status="adobe_return_confirmed",
                verification="adobe_confirmed", adobe_verified=True,
            ) or previous
        else:
            record = previous
    else:
        op_id = str(record.get("operation_id") or "")
        if not op_id:
            return {"status": "failed", "scope": "adobe_first_paris",
                    "retry": "verification_only"}
        self.storage.loan_return_transition(
            key, op_id, status="adobe_return_requested",
            verification="adobe_pending", adobe_verified=False,
        )
        _TRACE_LOG.info(
            "TRACE return core phase=adobe_return_requested ref=%s",
            return_ref(key),
        )
        try:
            result = self.return_adept_acquisition(acquisition_id)
            if result.get("ok") is not True or result.get("verification") != "confirmed":
                raise ValueError("ADEPT return not confirmed")
        except Exception:
            # An ADEPT runtime may supply an exception type containing local
            # profile/account information. Persist only one closed reason.
            record = self.storage.loan_return_transition(
                key, op_id, status="failed", verification="adobe_unavailable",
                adobe_verified=False, error_type="adept_return_unavailable",
            )
            return public_state(record or {"status": "failed"})
        record = self.storage.loan_return_transition(
            key, op_id, status="adobe_return_confirmed",
            verification="adobe_confirmed", adobe_verified=True,
        )
        _TRACE_LOG.info(
            "TRACE return core phase=adobe_return_confirmed ref=%s",
            return_ref(key),
        )

    if not record or record.get("adobe_verified") is not True:
        return public_state(record or {"status": "failed"})
    op_id = str(record.get("operation_id") or "")
    self.storage.loan_return_transition(
        key, op_id, status="paris_check_pending",
        verification="not_checked", adobe_verified=True,
    )
    paris = self.verify_paris_return_inventory(loan)
    status = (
        "paris_return_confirmed"
        if paris["verification"] == "provider_absent" else "partial"
    )
    finished = self.storage.loan_return_transition(
        key, op_id, status=status,
        verification=paris["verification"], adobe_verified=True,
    )
    return public_state(finished or {
        "status": status, "adobe_verified": True,
        "verification": paris["verification"],
    })
