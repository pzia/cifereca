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
    # The persisted journal grammar is source-neutral. BnF-specific identity,
    # credential, docbase and account checks remain above the read.
    from .borrow_claim_checkpoint_view import observed_borrow_claim_state
    state, consumed, verified = observed_borrow_claim_state(raw)
    return BnfBorrowClaimObservation(state, consumed, verified)


__all__ = ["BnfBorrowClaimObservation", "observe_bnf_borrow_claim"]
