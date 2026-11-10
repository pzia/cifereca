"""#4988: recover already-confirmed lender loans for a visible exact notice.

This read model needs neither a new eligibility observation nor a supplier GET:
a *previously confirmed* lender claim is a different fact from a fresh
permission to borrow. Recovering it must never rearm the one-shot POST.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Account, SearchResult
from .borrow_for_identity_progress import (
    CrossProfileBorrowProgress, observed_cross_profile_borrow_progress,
)
from .borrow_for_identity_placement_resolution import (
    LenderLocalPlacement, resolve_cross_profile_lender_placement,
)


@dataclass(frozen=True, slots=True, repr=False)
class ExistingLenderLoanCandidate:
    """Inert exact-account continuation, never a transfer/borrow permission."""

    _account: Account = field(repr=False)
    status: str  # present | awaiting_delivery
    loan: str = "confirmed"
    executable: bool = False
    retry_borrow: bool = False


def existing_lender_loan_candidates(
    service, *, exact_notice: SearchResult,
) -> tuple[ExistingLenderLoanCandidate, ...]:
    """Inspect only already-confirmed claims owned by other profiles.

    Used for a future explicit continuation after a crash or a late local
    delivery, not for discovery of new lenders. An unavailable/rebound
    source is silent; no raw claim, account ID or provider URL is returned.
    """
    try:
        requester = service.current_identity
        requester_id = requester.identity_id
        account_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        accounts = service.accounts
        if (type(exact_notice) is not SearchResult
                or exact_notice.medium != "digital"
                or type(requester_id) is not str or not requester_id
                or type(account_epoch) is not int or account_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0
                or type(accounts) not in (tuple, list)
                or len(accounts) > 512
                or type(exact_notice.source_provider) is not str
                or not exact_notice.source_provider
                or type(exact_notice.source_credential) is not str
                or not exact_notice.source_credential
                or type(exact_notice.record_id) is not str
                or not exact_notice.record_id):
            return ()
        # The notice is scoped to one exact provider/credential. Never use
        # another credential's loan against a shared title/ISBN/record ID.
        eligible = [
            a for a in accounts
            if type(a) is Account and a.is_primary is True
            and a.identity_id != requester_id
            and a.provider_id == exact_notice.source_provider
            and a.credential_id == exact_notice.source_credential
        ]
        if not eligible or len(eligible) > 64:
            return ()
        # An ambiguous account registration must not be resolved by order.
        keys = [
            (a.identity_id, a.provider_id, a.credential_id, a.unique_id)
            for a in eligible
        ]
        if len(set(keys)) != len(keys):
            return ()
        candidates: list[ExistingLenderLoanCandidate] = []
        for account in eligible:
            progress = observed_cross_profile_borrow_progress(
                service, requesting_identity_id=requester_id,
                borrowing_account=account, exact_notice=exact_notice,
            )
            if (type(progress) is not CrossProfileBorrowProgress
                    or progress.loan != "confirmed"
                    or progress.executable is not False
                    or progress.retry_borrow is not False):
                continue
            placement = resolve_cross_profile_lender_placement(
                service, requesting_identity_id=requester_id,
                borrowing_account=account, exact_notice=exact_notice,
            )
            if (type(placement) is not LenderLocalPlacement
                    or placement.loan != "confirmed"
                    or placement.executable is not False
                    or placement.retry_borrow is not False):
                continue
            if (placement.status == "present"
                    and type(placement._placement_id) is str
                    and placement._placement_id):
                status = "present"
            elif (placement.status == "unavailable"
                  and placement.reason_code == "lender_delivery_unverified"):
                status = "awaiting_delivery"
            else:
                continue
            candidates.append(ExistingLenderLoanCandidate(account, status))
        if (service.current_identity is not requester
                or service._account_context_epoch != account_epoch
                or service._source_context_epoch != source_epoch):
            return ()
        return tuple(candidates)
    except Exception:
        return ()


__all__ = ["ExistingLenderLoanCandidate", "existing_lender_loan_candidates"]
