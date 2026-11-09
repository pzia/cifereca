"""#4662: reconcile the *existing* lender claim without ever retrying a loan.

The normal Core/Provider borrowing claim belongs exclusively to the lender.
A receipt confirming that loan cannot attest legal transfer, delivery bytes,
ADEPT usability, or a right at the requesting profile. This module is a
non-mutating projection of an exact durable claim, not a second loan ledger.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Account, SearchResult
from .borrow_action_scope import borrow_claim_key


@dataclass(frozen=True, slots=True)
class CrossProfileBorrowProgress:
    """Closed and privacy-safe two-stage result for UI after an attempt."""

    loan: str  # not_attempted | confirmed | unknown
    delivery: str  # unavailable
    transfer: str  # not_attempted | blocked
    reason_code: str
    retry_borrow: bool = False
    # This status never authorizes a supplier POST or an A→B file copy.
    executable: bool = False


def observed_cross_profile_borrow_progress(
    service, *, requesting_identity_id: str,
    borrowing_account: Account, exact_notice: SearchResult,
) -> CrossProfileBorrowProgress:
    """Read only an exact lender's existing at-most-once checkpoint.

    The requesting reader is the destination, not a borrower or a current
    SourceReference. The lender's loan may remain confirmed even if a
    cross-profile copy is prohibited, blocked or not yet delivered.
    """
    uncertain = CrossProfileBorrowProgress(
        "unknown", "unavailable", "blocked", "lender_scope_unverified",
    )
    try:
        a, item = borrowing_account, exact_notice
        reader_epoch = getattr(service, "_account_context_epoch", None)
        source_epoch = getattr(service, "_source_context_epoch", None)
        reader_context = service.current_identity
        if (type(reader_epoch) is not int or type(source_epoch) is not int
                or type(requesting_identity_id) is not str
                or not requesting_identity_id
                or type(a) is not Account
                or type(item) is not SearchResult
                or not a.identity_id
                or requesting_identity_id == a.identity_id
                or getattr(reader_context, "identity_id", None)
                   != requesting_identity_id
                or service.current_identity is not reader_context
                or item.medium != "digital"
                or item.source_provider != a.provider_id
                or item.source_credential != a.credential_id
                or a.is_primary is not True
                or service.identity_by_id(requesting_identity_id) is None
                or service.identity_by_id(a.identity_id) is None
                or service.storage.identity_for_connection(
                    a.provider_id, a.credential_id,
                ) != a.identity_id
                or sum(row is a for row in service.accounts) != 1):
            return uncertain
        key = borrow_claim_key(a, item)
        if key is None:
            return uncertain

        def lender_binding_current() -> bool:
            """Read-only, exact owner+enabled guard; never a lending license."""
            if (getattr(service, "_account_context_epoch", None) != reader_epoch
                    or getattr(service, "_source_context_epoch", None) != source_epoch
                    or service.storage.identity_for_connection(
                        a.provider_id, a.credential_id,
                    ) != a.identity_id
                    or service.identity_by_id(a.identity_id) is None
                    or service.current_identity is not reader_context
                    or getattr(service.current_identity, "identity_id", None)
                       != requesting_identity_id
                    or sum(row is a for row in service.accounts) != 1):
                return False
            bindings = service.storage.provider_connections(
                identity_id=a.identity_id,
            )
            if type(bindings) is not list or len(bindings) > 512:
                return False
            exact = [
                binding for binding in bindings
                if type(binding) is dict
                and binding.get("identity_id") == a.identity_id
                and binding.get("provider_id") == a.provider_id
                and binding.get("credential_id") == a.credential_id
            ]
            # Even one enabled + one disabled duplicate is ambiguous: the
            # read model must never choose the convenient historical row.
            return (
                len(exact) == 1 and exact[0].get("enabled") is True
                and getattr(service, "_account_context_epoch", None) == reader_epoch
                and getattr(service, "_source_context_epoch", None) == source_epoch
                and service.current_identity is reader_context
                and getattr(service.current_identity, "identity_id", None)
                    == requesting_identity_id
            )

        # The source may be disabled while an old positive receipt remains
        # durable. Do not expose its state to a different profile.
        if not lender_binding_current():
            return uncertain

        # This scalar key is account+edition+collection scoped. Raw claim
        # bytes are never returned or logged, including loan identifiers,
        # receipt operation IDs and signed supplier links.
        raw = service.storage.metadata_get(key)
        checkpoint = service.storage.loan_borrow_state(key)
        # Recheck after the SQLite reads: disabling/rebinding a lender
        # during the observation must not publish even a positive status.
        if not lender_binding_current():
            return uncertain
        if raw is None:
            if checkpoint is not None:
                return uncertain
            return CrossProfileBorrowProgress(
                "not_attempted", "unavailable", "not_attempted",
                "loan_not_attempted",
            )
        if (type(checkpoint) is dict
                and checkpoint.get("status") == "confirmed"
                and checkpoint.get("request_accepted") is True
                and checkpoint.get("verification") == "positive_active_loan"
                and type(checkpoint.get("verified_loan_id")) is str
                and checkpoint["verified_loan_id"]):
            # A confirmed supplier loan is irreversible locally. Do not
            # repeat it to repair a missing ACSM, failed import or transfer.
            # Transfer is BLOCKED until a separate verified, lawful policy
            # and actual independent physical copy are supplied by the
            # intended executor. Never invoke materialize_to_identity here.
            return CrossProfileBorrowProgress(
                "confirmed", "unavailable", "blocked",
                "lender_loan_confirmed_transfer_unverified",
            )
        # request_sent/submitted/timeout/corrupt/blocked are all consumed
        # claims: never treat them as "no loan", and never propose replay.
        return CrossProfileBorrowProgress(
            "unknown", "unavailable", "blocked",
            "loan_result_not_confirmed",
        )
    except Exception:
        return uncertain


__all__ = [
    "CrossProfileBorrowProgress", "observed_cross_profile_borrow_progress",
]
