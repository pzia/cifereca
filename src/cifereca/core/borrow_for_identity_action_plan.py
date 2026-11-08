"""#4987: bind a clicked lender to one exact *inert* Core loan plan.

This is a preparatory read model, not permission, consent or a supplier
operation. No profile switch, claim, GET/POST, copy, ADEPT, or entitlement.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Account, SearchResult
from .borrow_action_scope import borrow_claim_key
from .borrow_for_identity_live import (
    ObservedBorrowOffer, _witness, revalidate_live_borrow_lender_selection,
)
from .borrow_for_identity_preview import BorrowForIdentityPreview
from .source_context import source_context_epoch


@dataclass(frozen=True, slots=True, repr=False)
class CrossProfileLenderActionPlan:
    status: str  # ready_for_consent | revoked
    reason_code: str
    requesting_identity_id: str | None = None
    borrowing_identity_id: str | None = None
    # Internal only. Never serialize or display source/account coordinates.
    _account: Account | None = field(default=None, repr=False)
    _notice: SearchResult | None = field(default=None, repr=False)
    _claim_key: str | None = field(default=None, repr=False)
    _context_epoch: int | None = field(default=None, repr=False)
    _source_epoch: int | None = field(default=None, repr=False)
    executable: bool = False
    transfer_permitted: bool = False


def prepare_cross_profile_lender_action_plan(
    service, *, captured_preview: BorrowForIdentityPreview,
    selected_option_id: str, expected_edition_id: str,
    observations: object, now: int | None = None,
) -> CrossProfileLenderActionPlan:
    """Re-resolve *only* the clicked lender's exact account and edition.

    The internal plan is deliberately insufficient to borrow. A future
    executor must verify consent, fresh permissions, lender action lease and
    an at-most-once claim under its own lock. No A-to-B copy is allowed here.
    """
    revoked = CrossProfileLenderActionPlan("revoked", "lender_plan_revoked")
    try:
        if (type(observations) not in (tuple, list) or len(observations) > 64
                or any(type(row) is not ObservedBorrowOffer for row in observations)
                or type(captured_preview) is not BorrowForIdentityPreview
                or captured_preview.executable is not False
                or type(selected_option_id) is not str
                or type(expected_edition_id) is not str):
            return revoked
        active_identity = service.current_identity
        context_epoch = service._account_context_epoch
        source_epoch = source_context_epoch(service)
        if (type(context_epoch) is not int or context_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0
                or getattr(active_identity, "identity_id", None)
                   != captured_preview.requesting_identity_id):
            return revoked
        selection = revalidate_live_borrow_lender_selection(
            service, captured_preview=captured_preview,
            selected_option_id=selected_option_id,
            expected_edition_id=expected_edition_id,
            observations=observations, now=now,
        )
        if (selection.status != "current" or selection.executable is not False
                or not selection.borrowing_identity_id
                or selection.borrowing_identity_id ==
                    captured_preview.requesting_identity_id):
            # Cross-profile only; own-profile borrowing uses existing flow.
            return revoked
        clicked = [option for option in captured_preview.options
                   if option.option_id == selected_option_id]
        if len(clicked) != 1:
            return revoked
        expected = clicked[0]._source_coordinates
        exact = []
        for row in observations:
            witness = _witness(
                service, row, context_epoch=context_epoch,
                source_epoch=source_epoch,
            )
            if not hasattr(witness, "account"):
                continue
            account, notice = witness.account, witness.notice
            coordinates = (
                account.identity_id, account.provider_id,
                account.credential_id, account.unique_id,
                notice.docbase, notice.record_id,
                notice.source_collection or "", witness.edition_id,
            )
            if (coordinates == expected and witness.edition_id == expected_edition_id
                    and account.identity_id == selection.borrowing_identity_id):
                exact.append((account, notice))
        # Duplicated or contradictory observation is never a first-match.
        if len(exact) != 1:
            return revoked
        account, notice = exact[0]
        key = borrow_claim_key(account, notice)
        if (key is None or service.storage.metadata_get(key) is not None
                or service.current_identity is not active_identity
                or service._account_context_epoch != context_epoch
                or source_context_epoch(service) != source_epoch):
            return revoked
        return CrossProfileLenderActionPlan(
            "ready_for_consent", "lender_exact_plan_unverified",
            requesting_identity_id=captured_preview.requesting_identity_id,
            borrowing_identity_id=account.identity_id,
            _account=account, _notice=notice, _claim_key=key,
            _context_epoch=context_epoch, _source_epoch=source_epoch,
        )
    except Exception:
        return revoked


__all__ = ["CrossProfileLenderActionPlan", "prepare_cross_profile_lender_action_plan"]
