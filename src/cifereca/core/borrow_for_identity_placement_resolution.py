"""#4987: resolve the lender's exact completed local acquisition without I/O.

A positive loan claim cannot identify a file by itself. This reader reuses
the Core's borrower-scoped local placement projection, which requires a
completed acquisition and an attested unique physical copy. It does not
borrow, fetch content, switch identity or send files.
"""
from __future__ import annotations

from .borrow_for_identity_copy_admission import navigation_epoch_current

from dataclasses import dataclass

from ..models import Account, SearchResult
from .borrow_for_identity_progress import observed_cross_profile_borrow_progress
from .library_projection import local_placement_for_source


@dataclass(frozen=True, slots=True, repr=False)
class LenderLocalPlacement:
    status: str  # present | unavailable
    reason_code: str
    _placement_id: str | None = None
    loan: str = "unknown"
    # A placement selection is neither a supplier ticket nor send consent.
    executable: bool = False
    retry_borrow: bool = False


def resolve_cross_profile_lender_placement(
    service, *, requesting_identity_id: str, borrowing_account: Account,
    exact_notice: SearchResult, scope_current=None,
) -> LenderLocalPlacement:
    """Identify A's single existing completed acquisition by exact account.

    It is deliberately read-only. The native 'Envoyer à' copy executor must
    revalidate the placement and obtain separate explicit send consent.
    """
    unavailable = LenderLocalPlacement("unavailable", "lender_scope_unverified")
    try:
        account, notice = borrowing_account, exact_notice
        context_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        if (type(requesting_identity_id) is not str or not requesting_identity_id
                or type(account) is not Account
                or type(notice) is not SearchResult
                or account.identity_id == requesting_identity_id
                or not callable(scope_current)
                or scope_current() is not True
                or type(context_epoch) is not int or context_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0
                or not account.identity_id or not account.provider_id
                or not account.credential_id
                or type(account.unique_id) is not str
                or notice.medium != "digital"
                or notice.source_provider != account.provider_id
                or notice.source_credential != account.credential_id
                or type(notice.record_id) is not str
                or not notice.record_id):
            return unavailable
        progress = observed_cross_profile_borrow_progress(
            service, requesting_identity_id=requesting_identity_id,
            borrowing_account=account, exact_notice=notice,
            scope_current=scope_current,
        )
        if (progress.loan != "confirmed"
                or progress.executable is not False
                or progress.retry_borrow is not False):
            return LenderLocalPlacement(
                "unavailable", "lender_loan_not_confirmed",
                loan=progress.loan if progress.loan in
                     ("confirmed", "unknown", "not_attempted") else "unknown",
            )
        # A revoked requester cannot initiate even a passive read of another
        # profile's precise local file placement.
        if scope_current() is not True:
            return LenderLocalPlacement(
                "unavailable", "lender_scope_unverified", loan="unknown",
            )
        # The public LibraryService facade has a frozen four-coordinate ABI.
        # Use the existing internal exact-account reader, not that facade.
        row = local_placement_for_source(
            service,
            provider_id=account.provider_id,
            credential_id=account.credential_id,
            item_id=notice.record_id,
            identity_id=account.identity_id,
            account_id=account.unique_id,
        )
        # Never choose the first row from duplicates, infer from a title or
        # expose another borrower's local path. The existing reader rejects
        # ambiguous receipts and verifies the actual local artifact.
        if (scope_current() is not True
                or not navigation_epoch_current(service, context_epoch, scope_current)
                or service._source_context_epoch != source_epoch
                or type(row) is not dict
                or row.get("identity_id") != account.identity_id
                or row.get("provider_id") != account.provider_id
                or row.get("source_item_id") != notice.record_id
                or row.get("state") != "present"
                or type(row.get("placement_id")) is not str
                or not row["placement_id"]):
            return LenderLocalPlacement(
                "unavailable", "lender_delivery_unverified", loan="confirmed",
            )
        return LenderLocalPlacement(
            "present", "lender_exact_local_placement_present",
            _placement_id=row["placement_id"], loan="confirmed",
        )
    except Exception:
        return unavailable


__all__ = ["LenderLocalPlacement", "resolve_cross_profile_lender_placement"]
