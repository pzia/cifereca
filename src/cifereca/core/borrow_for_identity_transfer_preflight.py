"""#4987: read-only boundary between a lender loan and a recipient copy.

A confirmed lender claim and a recorded placement are necessary observations,
never proof of copy permission, physical bytes, DRM compatibility or user
consent. This module must not invoke the local transfer executor.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Account, SearchResult
from .borrow_for_identity_progress import observed_cross_profile_borrow_progress


@dataclass(frozen=True, slots=True)
class CrossProfileTransferPreflight:
    loan: str
    delivery: str
    transfer: str
    reason_code: str
    executable: bool = False
    retry_borrow: bool = False


def preflight_cross_profile_loan_transfer(
    service, *, requesting_identity_id: str,
    borrowing_account: Account, exact_notice: SearchResult,
    lender_placement_id: str,
) -> CrossProfileTransferPreflight:
    """Inspect only an exact lender-owned local placement after a loan claim.

    No public provider contract currently proves inter-profile copy/usage
    rights for loaned content. Even a plausible local placement stays
    unverified and MUST NOT enable materialize_to_identity().
    """
    denied = CrossProfileTransferPreflight(
        "unknown", "unavailable", "blocked", "lender_scope_unverified",
    )
    try:
        if (type(requesting_identity_id) is not str
                or not requesting_identity_id
                or type(borrowing_account) is not Account
                or type(exact_notice) is not SearchResult
                or type(lender_placement_id) is not str
                or not 0 < len(lender_placement_id) <= 256
                or requesting_identity_id == borrowing_account.identity_id):
            return denied
        progress = observed_cross_profile_borrow_progress(
            service, requesting_identity_id=requesting_identity_id,
            borrowing_account=borrowing_account, exact_notice=exact_notice,
        )
        if (progress.loan != "confirmed"
                or progress.delivery != "unavailable"
                or progress.transfer != "blocked"
                or progress.executable is not False
                or progress.retry_borrow is not False):
            return CrossProfileTransferPreflight(
                progress.loan, "unavailable", "blocked",
                "lender_loan_not_confirmed",
            )
        # A claim may be confirmed but not yet delivered; no GET, download,
        # provider or filesystem access is triggered by this projection.
        current_reader = service.current_identity
        reader_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        placement = service.storage.placement_get(lender_placement_id)
        if (service.current_identity is not current_reader
                or service._account_context_epoch != reader_epoch
                or service._source_context_epoch != source_epoch
                or type(placement) is not dict
                or placement.get("identity_id") != borrowing_account.identity_id
                or placement.get("provider_id") != borrowing_account.provider_id
                or placement.get("source_item_id") != exact_notice.record_id
                or placement.get("state") != "present"
                or type(placement.get("artifact_id")) is not str
                or not placement["artifact_id"]):
            return CrossProfileTransferPreflight(
                "confirmed", "unavailable", "blocked",
                "lender_delivery_unverified",
            )
        # Matching SQLite coordinates can be stale, forged or protected;
        # their presence is not a legal/DRM attestation, nor physical proof.
        return CrossProfileTransferPreflight(
            "confirmed", "unavailable", "blocked",
            "lender_placement_observed_copy_rights_unverified",
        )
    except Exception:
        return denied


__all__ = [
    "CrossProfileTransferPreflight", "preflight_cross_profile_loan_transfer",
]
