"""#5833: exact local-copy consent, independent of subsequent navigation.

The existing local placement resolver and native copy executor remain the
only positive verification and mutation paths. The lease pins coordinates,
not permission to share supplier rights or to select a replacement file.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Account, SearchResult
from .borrow_action_scope import _fields, _valid_fields, borrow_claim_key
from .source_context import source_context_epoch


@dataclass(frozen=True, repr=False)
class AdmittedLenderCopy:
    _service: object = field(repr=False)
    _storage: object = field(repr=False)
    _requester: object = field(repr=False)
    _lender: object = field(repr=False)
    _requesting_identity_id: str
    _account: Account = field(repr=False)
    _notice: SearchResult = field(repr=False)
    _fields: tuple = field(repr=False)
    _claim_key: str = field(repr=False)
    _placement_id: str = field(repr=False)
    _source_epoch: int

    def __call__(self) -> bool:
        try:
            service, account, item = self._service, self._account, self._notice
            return (
                service.storage is self._storage
                and source_context_epoch(service) == self._source_epoch
                and service.identity_by_id(self._requesting_identity_id) is self._requester
                and service.identity_by_id(account.identity_id) is self._lender
                and self._requester.identity_id == self._requesting_identity_id
                and self._lender.identity_id == account.identity_id
                and account.is_primary is True
                and _fields(account, item) == self._fields
                and borrow_claim_key(account, item) == self._claim_key
                and sum(row is account for row in service.accounts) == 1
            )
        except Exception:
            return False


def navigation_epoch_current(service, epoch, scope_current) -> bool:
    """Old read/compatibility scopes retain navigation revocation.

    Only the exact admitted local-copy lease carries the independent source
    generation. All callers still invoke their scope guard, including after
    SQLite reads and after native copy.
    """
    if type(scope_current) is AdmittedLenderCopy:
        return scope_current._service is service and scope_current()
    return getattr(service, "_account_context_epoch", None) == epoch


def capture_lender_copy(
    service, *, requesting_identity_id: str, borrowing_account: Account,
    exact_notice: SearchResult, lender_placement_id: str,
) -> AdmittedLenderCopy | None:
    """Build a process-local scope from the explicitly confirmed coordinates."""
    try:
        account, item = borrowing_account, exact_notice
        if (type(requesting_identity_id) is not str or not requesting_identity_id
                or type(account) is not Account or type(item) is not SearchResult
                or account.identity_id == requesting_identity_id
                or account.is_primary is not True or item.medium != "digital"
                or type(lender_placement_id) is not str
                or not 0 < len(lender_placement_id) <= 256):
            return None
        fields = _fields(account, item)
        claim = borrow_claim_key(account, item)
        if not _valid_fields(fields) or claim is None:
            return None
        requester = service.identity_by_id(requesting_identity_id)
        lender = service.identity_by_id(account.identity_id)
        if requester is None or lender is None:
            return None
        lease = AdmittedLenderCopy(
            service, service.storage, requester, lender, requesting_identity_id,
            account, item, fields, claim, lender_placement_id,
            source_context_epoch(service),
        )
        return lease if lease() else None
    except Exception:
        return None


def execute_lender_copy(service, admitted) -> dict[str, object]:
    """Revalidate the same placement and delegate native copy, never reborrow."""
    blocked = {
        "status": "blocked", "loan": "unknown", "delivery": "unavailable",
        "transfer": "blocked", "reason_code": "lender_scope_unverified",
        "retry_borrow": False,
    }
    try:
        if (type(admitted) is not AdmittedLenderCopy
                or admitted._service is not service or not admitted()):
            return blocked
        from .borrow_for_identity_placement_resolution import (
            LenderLocalPlacement, resolve_cross_profile_lender_placement,
        )
        from .borrow_for_identity_transfer_executor import send_cross_profile_borrowed_placement

        scope = dict(
            requesting_identity_id=admitted._requesting_identity_id,
            borrowing_account=admitted._account, exact_notice=admitted._notice,
            scope_current=admitted,
        )
        resolution = resolve_cross_profile_lender_placement(service, **scope)
        if (type(resolution) is not LenderLocalPlacement
                or resolution.status != "present" or resolution.loan != "confirmed"
                or resolution.executable is not False or resolution.retry_borrow is not False
                or resolution._placement_id != admitted._placement_id
                or not admitted()):
            return blocked
        return send_cross_profile_borrowed_placement(
            service, lender_placement_id=admitted._placement_id,
            confirmed=True, **scope,
        )
    except Exception:
        return blocked
