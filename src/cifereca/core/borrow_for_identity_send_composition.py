"""#4987: explicitly copy a confirmed lender's already-delivered local book.

Composition only: resolve the exact A-owned physical placement, then delegate
to Core's existing independent native 'Envoyer à' copy. Never re-borrow,
download, activate another profile or transfer a supplier entitlement.
"""
from __future__ import annotations

from ..models import Account, SearchResult
from .borrow_for_identity_placement_resolution import (
    LenderLocalPlacement, resolve_cross_profile_lender_placement,
)


def send_confirmed_lender_local_copy(
    service, *, requesting_identity_id: str, borrowing_account: Account,
    exact_notice: SearchResult, confirmed: bool = False,
) -> dict[str, object]:
    """Explicit A->B send, failing closed on missing/stale local delivery."""
    blocked = {
        "status": "blocked", "loan": "unknown", "delivery": "unavailable",
        "transfer": "blocked", "reason_code": "lender_scope_unverified",
        "retry_borrow": False,
    }
    if confirmed is not True:
        return {**blocked, "reason_code": "transfer_confirmation_required"}
    observed_loan = "unknown"
    observed_delivery = "unavailable"
    try:
        reader = service.current_identity
        context_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        if (getattr(reader, "identity_id", None) != requesting_identity_id
                or type(context_epoch) is not int or context_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0):
            return blocked
        resolution = resolve_cross_profile_lender_placement(
            service, requesting_identity_id=requesting_identity_id,
            borrowing_account=borrowing_account, exact_notice=exact_notice,
        )
        if type(resolution) is LenderLocalPlacement and resolution.loan == "confirmed":
            observed_loan = "confirmed"
        if (type(resolution) is not LenderLocalPlacement
                or resolution.status != "present"
                or resolution.loan != "confirmed"
                or resolution.executable is not False
                or resolution.retry_borrow is not False
                or type(resolution._placement_id) is not str
                or not resolution._placement_id):
            loan = (resolution.loan if type(resolution) is LenderLocalPlacement
                    and resolution.loan in ("confirmed", "unknown", "not_attempted")
                    else "unknown")
            return {**blocked, "loan": loan,
                    "reason_code": "lender_delivery_unverified"}
        if (service.current_identity is not reader
                or service._account_context_epoch != context_epoch
                or service._source_context_epoch != source_epoch):
            return {**blocked, "loan": "confirmed",
                    "reason_code": "transfer_context_revoked"}
        observed_delivery = "present"
        # This executor independently revalidates the claim and placement;
        # the resolver's DTO never authorizes bypassing its checks.
        outcome = service.send_cross_profile_borrowed_placement(
            requesting_identity_id=requesting_identity_id,
            borrowing_account=borrowing_account, exact_notice=exact_notice,
            lender_placement_id=resolution._placement_id, confirmed=True,
        )
        if type(outcome) is not dict:
            return {**blocked, "loan": "confirmed",
                    "reason_code": "transfer_result_unverified"}
        # The native executor returns only a small public state object,
        # never source paths or supplier loan IDs. Forward closed values only.
        if (outcome.get("status") == "complete"
                and outcome.get("loan") == "confirmed"
                and outcome.get("delivery") == "present"
                and outcome.get("transfer") == "completed"
                and outcome.get("reason_code") == "independent_local_copy_completed"
                and outcome.get("retry_borrow") is False):
            return {
                "status": "complete", "loan": "confirmed",
                "delivery": "present", "transfer": "completed",
                "reason_code": "independent_local_copy_completed",
                "retry_borrow": False,
            }
        return {
            **blocked, "loan": "confirmed",
            "delivery": "present" if outcome.get("delivery") == "present"
            else "unavailable",
            "reason_code": "transfer_result_unverified",
        }
    except Exception:
        return {
            **blocked, "loan": observed_loan,
            "delivery": observed_delivery,
            "reason_code": "transfer_result_unverified",
        }


__all__ = ["send_confirmed_lender_local_copy"]
