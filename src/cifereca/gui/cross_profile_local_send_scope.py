"""#4988: resolve one post-loan lender account from a selected UI snapshot.

A spent borrowing claim invalidates pre-borrow eligibility revalidation. This
pure matcher only recovers the *previously clicked* identity/account/edition;
actual positive supplier loan and local delivery are revalidated by Core
before any separate file send. It does not create a permission or operation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Account, SearchResult
from ..core.borrow_for_identity_live import ObservedBorrowOffer
from ..core.borrow_for_identity_preview import (
    BorrowForIdentityOption, BorrowForIdentityPreview,
)


@dataclass(frozen=True, slots=True, repr=False)
class SelectedLenderLocalScope:
    account: Account = field(repr=False)
    notice: SearchResult = field(repr=False)
    executable: bool = False


def selected_lender_local_scope(
    service, *, captured_preview: BorrowForIdentityPreview,
    selected_option_id: str, expected_edition_id: str,
    observations: object,
) -> SelectedLenderLocalScope | None:
    """Recover exactly one clicked lender without re-borrowing or provider I/O."""
    try:
        if (type(captured_preview) is not BorrowForIdentityPreview
                or captured_preview.executable is not False
                or type(selected_option_id) is not str
                or type(expected_edition_id) is not str
                or type(observations) not in (list, tuple)
                or len(observations) > 64
                or any(type(row) is not ObservedBorrowOffer
                       for row in observations)):
            return None
        selected = [
            option for option in captured_preview.options
            if type(option) is BorrowForIdentityOption
            and option.option_id == selected_option_id
            and option.executable is False
            and option.edition_id == expected_edition_id
            and option.borrowing_identity_id != captured_preview.requesting_identity_id
        ]
        if len(selected) != 1:
            return None
        coords = selected[0]._source_coordinates
        if (type(coords) is not tuple or len(coords) != 8
                or any(type(value) is not str for value in coords)):
            return None
        accounts = [
            row for row in service.accounts
            if type(row) is Account
            and (row.identity_id, row.provider_id, row.credential_id,
                 row.unique_id) == coords[:4]
            and row.is_primary is True
        ]
        if len(accounts) != 1:
            return None
        account = accounts[0]
        exact = [
            row.notice for row in observations
            if type(row.edition_id) is str
            and row.edition_id == expected_edition_id
            and type(row.notice) is SearchResult
            and (row.notice.docbase, row.notice.record_id,
                 row.notice.source_collection or "", row.edition_id) == coords[4:]
            and row.notice.medium == "digital"
            and (row.notice.source_provider, row.notice.source_credential)
                == (account.provider_id, account.credential_id)
            and row.evidence is not None
            and (row.evidence.identity_id, row.evidence.provider_id,
                 row.evidence.credential_id, row.evidence.account_id,
                 row.evidence.branch) == (
                    account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id, row.notice.docbase,
                 )
            and row.edition_evidence is not None
            and (row.edition_evidence.identity_id,
                 row.edition_evidence.provider_id,
                 row.edition_evidence.credential_id,
                 row.edition_evidence.borrower_id,
                 row.edition_evidence.docbase,
                 row.edition_evidence.record_id,
                 row.edition_evidence.source_collection) == (
                    account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id,
                    row.notice.docbase, row.notice.record_id,
                    row.notice.source_collection,
                )
        ]
        if len(exact) != 1:
            return None
        return SelectedLenderLocalScope(account, exact[0])
    except Exception:
        return None


__all__ = ["SelectedLenderLocalScope", "selected_lender_local_scope"]
