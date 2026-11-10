"""#4987: explicit local send after the lender-owned placement is present.

Reuse the canonical "Envoyer à" executor; never re-borrow, export supplier
credentials, share an entitlement or synthesize a successful transfer receipt.
"""
from __future__ import annotations

from .borrow_for_identity_transfer_preflight import (
    preflight_cross_profile_loan_transfer,
)


def send_cross_profile_borrowed_placement(
    service, *, requesting_identity_id: str,
    borrowing_account, exact_notice, lender_placement_id: str,
    confirmed: bool = False,
) -> dict[str, object]:
    """One explicit A-to-B file copy via existing canonical materialization.

    Source validation, independent inode, idempotence and provenance stay
    owned by materialize_to_identity. No provider network call is made here.
    """
    blocked = {
        "status": "blocked", "loan": "unknown",
        "delivery": "unavailable", "transfer": "blocked",
        "reason_code": "transfer_scope_unverified", "retry_borrow": False,
    }
    if confirmed is not True:
        return {**blocked, "reason_code": "transfer_confirmation_required"}
    observed_loan = "unknown"
    observed_delivery = "unavailable"
    try:
        identity = service.current_identity
        identity_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        if (getattr(identity, "identity_id", None) != requesting_identity_id
                or type(identity_epoch) is not int
                or type(source_epoch) is not int):
            return blocked
        preflight = preflight_cross_profile_loan_transfer(
            service, requesting_identity_id=requesting_identity_id,
            borrowing_account=borrowing_account, exact_notice=exact_notice,
            lender_placement_id=lender_placement_id,
        )
        observed_loan = preflight.loan
        observed_delivery = preflight.delivery
        if (preflight.loan != "confirmed"
                or preflight.delivery != "present"
                or preflight.transfer != "ready_for_send"
                or preflight.executable is not True
                or preflight.retry_borrow is not False):
            return {
                **blocked, "loan": preflight.loan,
                "reason_code": preflight.reason_code,
            }
        if (service.current_identity is not identity
                or service._account_context_epoch != identity_epoch
                or service._source_context_epoch != source_epoch):
            return {**blocked, "loan": "confirmed",
                    "reason_code": "transfer_context_revoked"}
        # Actual filesystem and ownership checks are enforced *inside*
        # the canonical send under its acquisition lock, not by this
        # read-only preflight. Never use a supplier download or loan POST.
        receipt = service.materialize_to_identity(
            lender_placement_id, requesting_identity_id,
        )
        if (type(receipt) is not dict
                or receipt.get("status") not in ("complete", "unchanged")):
            return {
                **blocked, "loan": "confirmed", "delivery": "present",
                "reason_code": "transfer_result_unverified",
            }
        # Completion is about the physical copy only. A provider licence
        # remains scoped to the lender; no recipient entitlement is issued.
        return {
            "status": "complete", "loan": "confirmed",
            "delivery": "present", "transfer": "completed",
            "reason_code": "independent_local_copy_completed",
            "retry_borrow": False,
        }
    except Exception:
        # The source/copy may be stale, revoked or already completed.
        # Re-run the *copy* explicitly if needed, never the supplier borrow.
        return {
            **blocked, "loan": observed_loan,
            "delivery": observed_delivery,
            "reason_code": "transfer_result_unverified",
        }


__all__ = ["send_cross_profile_borrowed_placement"]
