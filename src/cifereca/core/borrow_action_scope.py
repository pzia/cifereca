"""Core #2055: inert scope lease for a *new* loan's selected catalogue notice.

A catalogue button or this lease is never proof of a right to borrow.
No provider getter, HTTP, mutation, credential or consent is accessed here.
The caller must additionally establish the fresh permission, explicit manual
consent and a persistent at-most-once claim before a lender POST.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any

from ..models import Account, SearchResult
from .loan_action_scope import _inert_session_accounts, _inert_session_state


def _fields(account: Account, item: SearchResult):
    return (
        account.identity_id, account.provider_id, account.credential_id,
        account.unique_id, item.id, item.record_id, item.docbase, item.medium,
        item.source_provider, item.source_credential,
        item.source_collection,
    )


def _valid_fields(fields: tuple[object, ...]) -> bool:
    return (len(fields) == 11
            and all(type(x) is str and len(x) <= 1024 and "\x00" not in x
                    for x in fields[:10])
            and (fields[10] is None or (
                type(fields[10]) is str and 0 < len(fields[10]) <= 1024
                and "\x00" not in fields[10]))
            and all(fields[n] for n in (0, 1, 2, 4, 5, 6, 7, 8, 9))
            and fields[1] == fields[8]
            and fields[2] == fields[9]
            and fields[7] in {"digital", "physical"})


def borrow_claim_key(account: Account, item: SearchResult) -> str | None:
    """Non-reversible, scope-bound replay key for *one* edition and borrower.

    The key intentionally remains stable across session reconnects and restarts.
    If upstream later supports borrowing the same edition after a completed
    cycle, that new attested cycle needs a separately versioned strategy;
    the caller must never auto-reset this claim based on a partial inventory.
    """
    if type(account) is not Account or type(item) is not SearchResult:
        return None
    values = _fields(account, item)
    if not _valid_fields(values):
        return None
    # SearchResult.id may be a temporary UI/list key. Two catalogue rows
    # pointing to the *same* edition of the same borrower must share one
    # atomic claim. source_collection partitions independent subcatalogues.
    collection = item.source_collection
    if collection is not None and (
            type(collection) is not str or not collection
            or len(collection) > 1024 or "\x00" in collection):
        return None
    stable = (
        account.identity_id, account.provider_id, account.credential_id,
        account.unique_id, item.docbase, item.medium, item.record_id,
        collection,
    )
    digest = sha256(json.dumps(
        stable, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return "loan.borrow.v1:" + digest


@dataclass(frozen=True, slots=True, repr=False)
class BorrowActionScopeLease:
    """Non-authorizing, ABA-resistant reference to one selected notice."""
    _account: Account = field(repr=False)
    _item: SearchResult = field(repr=False)
    _session: Any = field(repr=False)
    _expected_identity_id: str
    _original_fields: tuple[str, ...] = field(repr=False)
    _account_is_primary: bool
    _context_epoch: int
    _provider_epoch: object = field(default=None, repr=False)

    def current(self, service: object) -> bool:
        try:
            account, item, session = self._account, self._item, self._session
            state = _inert_session_state(session)
            if (type(account) is not Account or type(item) is not SearchResult
                    or state is None
                    or type(self._context_epoch) is not int
                    or type(getattr(service, "_account_context_epoch", None)) is not int
                    or service._account_context_epoch != self._context_epoch
                    or state.get("_epoch") != self._provider_epoch
                    or state.get("_closed") is True
                    or state.get("connected") is not True):
                return False
            values = _fields(account, item)
            if (not _valid_fields(values) or values != self._original_fields
                    or account.is_primary is not self._account_is_primary
                    or values[0] != self._expected_identity_id
                    or state.get("provider_id") != values[1]
                    or state.get("credential_id") != values[2]):
                return False
            identity_id, provider, credential, _borrower = values[:4]
            # Ownership alone survives a disabled connection and cannot
            # authorize the lender's mutating POST. Require exactly one
            # active binding; duplicate enabled/disabled rows fail closed.
            connections = service.storage.provider_connections(
                identity_id=identity_id,
            )
            if type(connections) is not list or len(connections) > 512:
                return False
            exact = [
                row for row in connections
                if type(row) is dict
                and row.get("identity_id") == identity_id
                and row.get("provider_id") == provider
                and row.get("credential_id") == credential
            ]
            if len(exact) != 1 or exact[0].get("enabled") is not True:
                return False
            identity = getattr(service, "current_identity", None)
            if (getattr(identity, "identity_id", None) != identity_id
                    or service.identity_by_id(identity_id) is None
                    or service.storage.identity_for_connection(
                        provider, credential) != identity_id
                    or service._sessions.get(
                        service._session_key(provider, credential),
                    ) is not session):
                return False
            registered = _inert_session_accounts(session)
            if (registered is None
                    or sum(x is account for x in registered) != 1
                    or sum(x is account for x in service.accounts) != 1
                    or service._session_for_source(provider, credential) is not session):
                return False
            return True
        except Exception:
            return False


def capture_borrow_action_scope(
    service: object, item: SearchResult, account: Account,
    session: object, *, expected_identity_id: str,
) -> BorrowActionScopeLease | None:
    """Capture *before* all I/O, re-check after each read and before the POST.

    No permission or explicit confirmation is inferred from this guard.
    """
    if (type(account) is not Account or type(item) is not SearchResult
            or type(expected_identity_id) is not str
            or not expected_identity_id or type(account.is_primary) is not bool):
        return None
    fields = _fields(account, item)
    epoch = getattr(service, "_account_context_epoch", None)
    session_state = _inert_session_state(session)
    if (not _valid_fields(fields) or type(epoch) is not int
            or session_state is None):
        return None
    lease = BorrowActionScopeLease(
        _account=account, _item=item, _session=session,
        _expected_identity_id=expected_identity_id,
        _original_fields=fields, _account_is_primary=account.is_primary,
        _context_epoch=epoch, _provider_epoch=session_state.get("_epoch"),
    )
    return lease if lease.current(service) else None


__all__ = [
    "BorrowActionScopeLease", "capture_borrow_action_scope",
    "borrow_claim_key",
]
