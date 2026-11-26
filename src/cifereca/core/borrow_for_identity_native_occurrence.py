"""#4988: exact original authenticated Search occurrence, not a loan offer.

Reuse the existing pre-call owner/account/session capture, independently of
the optional web-link parser. The receipt is ephemeral and belongs to the
original provider DTO; neither C bibliography nor a cached copy recreates it.
No provider I/O, rights, new writer or claim is involved.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Account, SearchResult
from .borrow_action_scope import _fields, _valid_fields, borrow_claim_key
from .borrow_for_identity_discovery import _notice_binding
from .source_context import source_context_mutation

_TOKEN = object()
_MARKER = "_core_native_borrow_occurrence"
_MAX_RETAINED = 2048


@dataclass(frozen=True, slots=True, repr=False)
class _Occurrence:
    token: object = field(repr=False)
    row: SearchResult = field(repr=False)
    call: object = field(repr=False)
    key: object = field(repr=False)
    fields: tuple = field(repr=False)
    notice_binding: tuple = field(repr=False)

    def __deepcopy__(self, memo):
        return self


def _native_call(service, call):
    from .notice_c_private_search_web import _Call
    from .notice_c_authenticated_web_choices import (
        _Binding, _native_reader_scope, _native_source_document,
    )
    return (type(call) is _Call
            and _native_reader_scope(call.scope, service)
            and type(call.binding) is _Binding
            and type(call.binding.account) is Account
            and _native_source_document(call.binding.document)
            and call.binding.current(service, call.scope))


def stamp_native_borrow_occurrences(service, call, rows):
    """Attach only to unique exact native rows of an already captured call."""
    from .notice_c_private_search_web import _Call, _BatchCall, _source_key
    if type(rows) not in (list, tuple):
        return
    for row in rows:
        if type(row) is SearchResult:
            row.__dict__.pop(_MARKER, None)
    if type(rows) is not list or len(rows) > 10000:
        return
    try:
        calls = (call,) if type(call) is _Call else (
            call.calls if type(call) is _BatchCall else ())
        if type(calls) is not tuple or not 1 <= len(calls) <= 64:
            return
        by_account = {}
        for captured in calls:
            if not _native_call(service, captured):
                continue
            doc = captured.binding.document
            coords = (doc.provider_id, doc.credential_id, doc.account_id)
            by_account[coords] = captured if coords not in by_account else None
        pending = {}
        for row in rows:
            if type(row) is not SearchResult:
                continue
            coords = (row.source_provider, row.source_credential, row.source_account_id)
            if not all(type(v) is str and 0 < len(v) <= 512 for v in coords):
                continue
            captured = by_account.get(coords)
            if captured is None:
                continue
            key = _source_key(row, captured.scope.identity_id)
            if key is None:
                continue
            # Ambiguous repeated source observations fail closed, even if
            # only one of the duplicates would otherwise be a lender.
            if key in pending:
                pending[key] = None
                continue
            account = captured.binding.account
            fields = _fields(account, row)
            binding = _notice_binding(account, row)
            pending[key] = (_Occurrence(_TOKEN, row, captured, key, fields, binding)
                            if (account.is_primary is True
                                and row.medium == "digital"
                                and _valid_fields(fields)
                                and binding is not None
                                and borrow_claim_key(account, row) is not None)
                            else None)
        for receipt in pending.values():
            if receipt is not None:
                receipt.row.__dict__[_MARKER] = receipt
        # Production service owns this bounded, nonserialized inventory.
        # A provider result is not durable authority, and a later Search
        # must not expose credentials or copy a foreign raw result into GTK.
        with source_context_mutation(service):
            inventory = getattr(service, "_native_borrow_occurrences", None)
            if type(inventory) is dict:
                for key, receipt in pending.items():
                    inventory.pop(key, None)
                    if receipt is not None:
                        inventory[key] = receipt.row
                while len(inventory) > _MAX_RETAINED:
                    inventory.pop(next(iter(inventory)))
    except (AttributeError, TypeError, ValueError, LookupError, RuntimeError):
        # This additional passive witness cannot abort ordinary Search.
        for row in rows:
            if type(row) is SearchResult:
                row.__dict__.pop(_MARKER, None)


def native_borrow_occurrence_document(service, row, *, owner):
    """Worker-only validation of the exact original native occurrence.

    Return an exact source DocumentRef, not a C relation or authorization.
    A separate admitted canonical membership must still bind this source
    to the reader's visible C before it can enter a multi-source roster.
    """
    from .borrow_for_identity_public_branch import public_branch_occurrence_document
    public = public_branch_occurrence_document(service, row, owner=owner)
    if public is not None:
        return public
    from .notice_c_private_search_web import _source_key, _read_source_key
    from .notice_cover_projection import notice_cover_document_ref
    from .notice_source_observation import NoticeSourceKey, _valid_key
    if type(row) is not SearchResult or type(owner) is not str or not owner:
        return None
    try:
        receipt = row.__dict__.get(_MARKER)
        if (type(receipt) is not _Occurrence or receipt.token is not _TOKEN
                or receipt.row is not row or not _native_call(service, receipt.call)
                or receipt.call.scope.identity_id != owner
                or type(receipt.key) is not NoticeSourceKey or not _valid_key(receipt.key)
                or type(receipt.fields) is not tuple or not _valid_fields(receipt.fields)
                or type(receipt.notice_binding) is not tuple
                or len(receipt.notice_binding) != 3
                or any(type(v) not in (str, type(None)) for v in receipt.notice_binding)):
            return None
        account = receipt.call.binding.account
        key = _read_source_key(row, owner)
        binding = _notice_binding(account, row)
        fields = _fields(account, row)
        if (key is None or key != receipt.key or _source_key(row, owner) != key
                or account.is_primary is not True or row.medium != "digital"
                or not _valid_fields(fields) or fields != receipt.fields
                or binding is None or binding != receipt.notice_binding
                or borrow_claim_key(account, row) is None
                or not receipt.call.binding.current(service, receipt.call.scope)):
            return None
        # Supersession/eviction must revoke a held private roster exactly
        # as for the public-branch path. A still-connected session alone
        # is not evidence that this row remains the retained occurrence.
        with source_context_mutation(service):
            inventory = getattr(service, "_native_borrow_occurrences", None)
            if type(inventory) is not dict or inventory.get(key) is not row:
                return None
        return notice_cover_document_ref(key)
    except (AttributeError, TypeError, ValueError, LookupError, RuntimeError):
        return None


def retained_native_borrow_occurrences(service, *, source_refs):
    """Bounded snapshot of original rows matching already admitted C refs.

    No new GET, serialization, login or navigation. The caller must still
    validate each row's original session, exact source and C membership.
    """
    if (type(source_refs) is frozenset
            and 0 < len(source_refs) <= 64
            and all(type(v) is str for v in source_refs)):
        from .notice_cover_projection import notice_cover_document_ref
        from .notice_source_observation import NoticeSourceKey, _valid_key
        try:
            with source_context_mutation(service):
                inventory = getattr(service, "_native_borrow_occurrences", None)
                if type(inventory) is not dict or len(inventory) > _MAX_RETAINED:
                    return ()
                snapshot = tuple(inventory.items())
            from .borrow_for_identity_public_branch import PublicBranchOccurrenceKey
            rows = []
            for key, row in snapshot:
                if type(row) is not SearchResult:
                    continue
                if type(key) is NoticeSourceKey and _valid_key(key):
                    source, owner = key, key.identity_id
                elif type(key) is PublicBranchOccurrenceKey and _valid_key(key.source):
                    source, owner = key.source, key.owner
                else:
                    continue
                doc = notice_cover_document_ref(source)
                if doc.source_key not in source_refs:
                    continue
                if native_borrow_occurrence_document(service, row, owner=owner) == doc:
                    rows.append(row)
                if len(rows) > 64:
                    return ()
            return tuple(rows)
        except Exception:
            return ()
    return ()


__all__ = ["stamp_native_borrow_occurrences", "native_borrow_occurrence_document",
           "retained_native_borrow_occurrences"]
