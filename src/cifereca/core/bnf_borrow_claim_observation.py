"""P1 #4082: inert, privacy-safe view of the existing BnF borrow checkpoint.

The old one-shot loan.borrow.v1 claim is *not* a renewable entitlement.
This observer performs a single, bounded SQLite metadata read for an exact
identity/account/edition. It never calls a provider, removes a claim, mints a
new generation or authorizes a second borrow POST. A separate explicit-user
cycle protocol with fresh *complete* inventory is still required for rearm.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import Account, SearchResult
from .borrow_action_scope import borrow_claim_key


@dataclass(frozen=True, slots=True)
class BnfBorrowClaimObservation:
    """Closed public states; never includes key, operation/loan ID or account."""

    state: str
    claim_consumed: bool | None
    provider_verified: bool
    retry_authorized: bool = False


def observe_bnf_borrow_claim(
    *, storage: Any, account: Account, item: SearchResult,
    expected_identity_id: str,
) -> BnfBorrowClaimObservation:
    """Look up a prior *exact* borrower/edition claim without mutation.

    An absent record is evidence only that this checkpoint does not exist.
    It is not an attestation of lender eligibility, quota, full loan
    coverage, download rights or permission to submit a new POST.
    """
    unknown = BnfBorrowClaimObservation("scope_unverified", None, False)
    if (type(expected_identity_id) is not str or not expected_identity_id
            or type(account) is not Account
            or type(item) is not SearchResult
            or account.identity_id != expected_identity_id
            or account.provider_id != "bnf-pret"
            or item.source_provider != "bnf-pret"
            or item.docbase != "BNF_PRET"
            or item.medium != "digital"
            or item.source_credential != account.credential_id):
        return unknown
    key = borrow_claim_key(account, item)
    if key is None:
        return unknown
    try:
        raw = storage.metadata_get(key)
    except Exception:
        # A failed read cannot prove that no attempt exists.
        return BnfBorrowClaimObservation("read_unavailable", None, False)
    if raw is None:
        return BnfBorrowClaimObservation("no_checkpoint", False, False)
    if type(raw) is not str or len(raw) > 4096:
        return BnfBorrowClaimObservation("opaque_consumed", True, False)
    try:
        import json
        row = json.loads(raw)
    except (TypeError, ValueError):
        return BnfBorrowClaimObservation("opaque_consumed", True, False)
    if (type(row) is not dict or type(row.get("schema")) is not int
            or row["schema"] != 1 or row.get("operation") != "loan.borrow"
            or type(row.get("operation_id")) is not str
            or not row["operation_id"]):
        return BnfBorrowClaimObservation("opaque_consumed", True, False)
    status = row.get("status")
    if status == "confirmed":
        confirmed = (
            row.get("request_accepted") is True
            and row.get("verification") == "positive_active_loan"
            and type(row.get("verified_loan_id")) is str
            and 0 < len(row["verified_loan_id"]) <= 128
            and all(c.isascii() and (c.isalnum() or c in "_-")
                    for c in row["verified_loan_id"])
        )
        return BnfBorrowClaimObservation(
            "confirmed" if confirmed else "opaque_consumed", True,
            confirmed,
        )
    if type(status) is str and status in {
        "request_sent", "submitted", "verification_unavailable", "blocked",
    }:
        return BnfBorrowClaimObservation(status, True, False)
    return BnfBorrowClaimObservation("opaque_consumed", True, False)


__all__ = ["BnfBorrowClaimObservation", "observe_bnf_borrow_claim"]
