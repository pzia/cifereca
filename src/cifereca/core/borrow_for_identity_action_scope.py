"""#4987: non-authorizing Core lease for a cross-profile lender action.

The active reader remains the requester. The lender must be an exact,
already-connected identity-owned session; there is never a profile switch,
network call, provider getter, durable claim or transfer in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Account, SearchResult
from .borrow_action_scope import _fields, _valid_fields, borrow_claim_key
from .loan_action_scope import _inert_session_accounts, _inert_session_state
from .source_context import source_context_epoch


@dataclass(frozen=True, slots=True, repr=False)
class CrossProfileBorrowScopeLease:
    _account: Account = field(repr=False)
    _notice: SearchResult = field(repr=False)
    _session: object = field(repr=False)
    _requester: object = field(repr=False)
    _requester_identity_id: str
    _account_fields: tuple[object, ...] = field(repr=False)
    _claim_key: str = field(repr=False)
    _context_epoch: int
    _source_epoch: int
    _session_epoch: object = field(repr=False)

    def current(self, service: object) -> bool:
        """Validate both identities and exact account without mutating them."""
        try:
            account, notice, session = self._account, self._notice, self._session
            if (type(account) is not Account or type(notice) is not SearchResult
                    or type(self._requester_identity_id) is not str
                    or not self._requester_identity_id
                    or account.identity_id == self._requester_identity_id
                    or service.current_identity is not self._requester
                    or getattr(self._requester, "identity_id", None)
                        != self._requester_identity_id
                    or service.identity_by_id(self._requester_identity_id) is None
                    or service.identity_by_id(account.identity_id) is None
                    or service._account_context_epoch != self._context_epoch
                    or source_context_epoch(service) != self._source_epoch):
                return False
            fields = _fields(account, notice)
            if (not _valid_fields(fields) or fields != self._account_fields
                    or account.is_primary is not True
                    or notice.medium != "digital"
                    or borrow_claim_key(account, notice) != self._claim_key):
                return False
            state = _inert_session_state(session)
            session_accounts = _inert_session_accounts(session)
            if (state is None or session_accounts is None
                    or state.get("_epoch") != self._session_epoch
                    or state.get("connected") is not True
                    or state.get("_closed") is True
                    or state.get("provider_id") != account.provider_id
                    or state.get("credential_id") != account.credential_id
                    or sum(row is account for row in session_accounts) != 1
                    or sum(row is account for row in service.accounts) != 1
                    or service._sessions.get(service._session_key(
                        account.provider_id, account.credential_id,
                    )) is not session):
                return False
            # The SQLite owner and binding must agree exactly. Neither a
            # disabled/active duplicate nor an active/active duplicate is an
            # acceptable lender, even if one is marked as primary.
            if service.storage.identity_for_connection(
                account.provider_id, account.credential_id,
            ) != account.identity_id:
                return False
            connections = service.storage.provider_connections(
                identity_id=account.identity_id,
            )
            if type(connections) is not list or len(connections) > 512:
                return False
            exact = [
                row for row in connections if type(row) is dict
                and row.get("identity_id") == account.identity_id
                and row.get("provider_id") == account.provider_id
                and row.get("credential_id") == account.credential_id
            ]
            if len(exact) != 1 or exact[0].get("enabled") is not True:
                return False
            # A hostile/lazy storage callback can rebind the account or
            # session without updating Core epochs. Recheck the complete
            # local scalar/registry boundary *after* reading SQLite too.
            fresh_state = _inert_session_state(session)
            fresh_rows = _inert_session_accounts(session)
            return (
                service.current_identity is self._requester
                and service._account_context_epoch == self._context_epoch
                and source_context_epoch(service) == self._source_epoch
                and _fields(account, notice) == self._account_fields
                and borrow_claim_key(account, notice) == self._claim_key
                and account.is_primary is True
                and fresh_state is not None
                and fresh_state.get("_epoch") == self._session_epoch
                and fresh_state.get("connected") is True
                and fresh_state.get("_closed") is not True
                and fresh_rows is not None
                and sum(row is account for row in fresh_rows) == 1
                and sum(row is account for row in service.accounts) == 1
                and service._sessions.get(service._session_key(
                    account.provider_id, account.credential_id,
                )) is session
            )
        except Exception:
            return False


def capture_cross_profile_borrow_scope(
    service: object, *, requesting_identity_id: str,
    borrowing_account: Account, exact_notice: SearchResult,
    lender_session: object,
) -> CrossProfileBorrowScopeLease | None:
    """Capture a necessary pre-POST boundary, never authorization or consent."""
    if (type(requesting_identity_id) is not str
            or not requesting_identity_id
            or type(borrowing_account) is not Account
            or type(exact_notice) is not SearchResult
            or borrowing_account.identity_id == requesting_identity_id):
        return None
    state = _inert_session_state(lender_session)
    try:
        requester = service.current_identity
        fields = _fields(borrowing_account, exact_notice)
        claim = borrow_claim_key(borrowing_account, exact_notice)
        context = service._account_context_epoch
        source = source_context_epoch(service)
        if (state is None or not _valid_fields(fields)
                or claim is None
                or getattr(requester, "identity_id", None)
                    != requesting_identity_id
                or type(context) is not int or context < 0
                or type(source) is not int or source < 0
                ):
            # Some provider sessions have no private local _epoch. Exact
            # object/session registry checks and Core's source epoch remain
            # mandatory; absence is not permission to skip those guards.
            return None
        lease = CrossProfileBorrowScopeLease(
            _account=borrowing_account, _notice=exact_notice,
            _session=lender_session, _requester=requester,
            _requester_identity_id=requesting_identity_id,
            _account_fields=fields, _claim_key=claim,
            _context_epoch=context, _source_epoch=source,
            _session_epoch=state.get("_epoch"),
        )
        return lease if lease.current(service) else None
    except Exception:
        return None


__all__ = ["CrossProfileBorrowScopeLease", "capture_cross_profile_borrow_scope"]
