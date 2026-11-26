"""#4988: native primary-account Search occurrences on a declared public branch.

The public edition relation is descriptive only. Capture the original account
and session BEFORE the already scheduled Search. The receipt is attached only
to the exact returned native row. C membership, discovery, consent and the
ordinary claim/executor remain separate. No private web binding is weakened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from ..models import Account, SearchResult
from ..providers.base import ProviderDescriptor
from .borrow_action_scope import _fields, _valid_fields, borrow_claim_key
from .borrow_for_identity_discovery import _notice_binding
from .loan_action_scope import _inert_session_accounts, _inert_session_state
from .notice_c_exact_source_lookup import _valid_coordinates
from .notice_cover_projection import notice_cover_document_ref
from .notice_search_cover_producer import _coordinate_digest
from .notice_source_observation import NoticeSourceKey, _valid_key
from .source_context import source_context_mutation

_TOKEN = object()
_MARKER = "_core_public_branch_borrow_occurrence"


def _branches(descriptor):
    if type(descriptor) is not ProviderDescriptor or descriptor.state != "ready":
        return ()
    return _validated_branches(descriptor.authenticated_search_public_edition_branches_v1)


def _validated_branches(raw):
    if type(raw) is not tuple or not 1 <= len(raw) <= 64:
        return ()
    def valid(value):
        return type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", value)
    if any(type(r) is not tuple or len(r) != 4
           or not valid(r[0]) or not valid(r[1])
           or type(r[2]) is not str or r[2] != "digital"
           or (r[3] is not None and not valid(r[3])) for r in raw):
        return ()
    return raw if len(set(raw)) == len(raw) else ()


@dataclass(frozen=True, slots=True, repr=False)
class _Call:
    scope: object
    session: object
    account: Account
    descriptor: ProviderDescriptor
    coordinates: tuple
    branches: tuple
    session_epoch: object
    connection_epoch: int


@dataclass(frozen=True, slots=True, repr=False)
class PublicBranchOccurrenceKey:
    owner: str
    provider: str
    credential: str
    account_id: str
    source: NoticeSourceKey


@dataclass(frozen=True, slots=True, repr=False)
class _Receipt:
    token: object
    row: SearchResult
    call: _Call
    key: PublicBranchOccurrenceKey
    fields: tuple
    binding: tuple

    def __deepcopy__(self, memo):
        return self


def _current(service, call):
    from .notice_c_authenticated_web_choices import _native_reader_scope
    if type(call) is not _Call or not _native_reader_scope(call.scope, service):
        return False
    try:
        coords = call.coordinates
        if (type(coords) is not tuple or len(coords) != 4
                or any(type(v) is not str for v in coords)
                or not all(coords[:3]) or coords[3] != ""
                or coords[0] != call.scope.identity_id
                or coords[2] == "__public__"
                or type(call.account) is not Account
                or type(call.connection_epoch) is not int or call.connection_epoch < 1
                or not _validated_branches(call.branches)
                or not _branches(call.descriptor) or _branches(call.descriptor) != call.branches
                or call.descriptor.id != coords[1]
                or call.descriptor.capabilities.authentication is not True
                or type(call.descriptor.existing_primary_loan_account_alias) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}",
                                call.descriptor.existing_primary_loan_account_alias) is None):
            return False
        state = _inert_session_state(call.session)
        accounts = _inert_session_accounts(call.session)
        registered = service.accounts_for_identity(coords[0])
        def exact(account):
            return type(account) is Account and (
                account.identity_id, account.provider_id, account.credential_id, account.unique_id,
            ) == coords
        return (
            call.scope.current(service)
            and state is not None and accounts is not None
            and state.get("connected") is True and state.get("_closed") is not True
            and state.get("descriptor") is call.descriptor
            and state.get("provider_id") == coords[1]
            and state.get("credential_id") == coords[2]
            and type(call.session_epoch) in (str, int, type(None))
            and type(state.get("_epoch")) is type(call.session_epoch)
            and state.get("_epoch") == call.session_epoch
            and service._sessions.get(service._session_key(coords[1], coords[2])) is call.session
            and exact(call.account) and call.account.is_primary is True
            and sum(exact(a) for a in accounts) == 1
            and sum(a is call.account for a in accounts) == 1
            and sum(type(a) is Account and a.is_primary is True for a in accounts) == 1
            and type(registered) in (tuple, list) and len(registered) <= 512
            and sum(exact(a) for a in registered) == 1
            and sum(a is call.account for a in registered) == 1
            and service._provider_enabled_for_identity(coords[1], coords[0]) is True
            and service.storage.provider_scope_account_bound(
                identity_id=coords[0], provider_id=coords[1],
                credential_id=coords[2], account_id=coords[3],
            ) is True
            and service.storage.provider_connection_epoch_current(
                coords[1], coords[2], coords[0], call.connection_epoch,
            ) is True
            and call.scope.current(service)
        )
    except Exception:
        return False


def capture_public_branch_borrow_call(service, session, identity_id):
    """No login or supplier getter: primary empty id remains the exact SQL id."""
    from .search_orchestration import capture_search_read_scope
    try:
        state = _inert_session_state(session)
        accounts = _inert_session_accounts(session)
        if state is None or accounts is None or not 1 <= len(accounts) <= 64:
            return None
        descriptor = state.get("descriptor")
        branches = _branches(descriptor)
        if not branches:
            return None
        candidates = tuple(a for a in accounts if type(a) is Account and a.is_primary is True)
        if len(candidates) != 1:
            return None
        account = candidates[0]
        # For an already authorized public-only Search, capture its actual
        # owner rather than the requesting reader. _current still checks
        # that exact registered account, SQL owner and connection epoch.
        if identity_id is None:
            identity_id = account.identity_id
        if account.identity_id != identity_id or account.unique_id != "":
            return None
        coords = (identity_id, account.provider_id, account.credential_id, account.unique_id)
        scope = capture_search_read_scope(service, identity_id)
        call = _Call(scope, session, account, descriptor, coords, branches,
                     state.get("_epoch"), service.storage.provider_connection_epoch(
                         coords[1], coords[2], coords[0],
                     ))
        return call if _current(service, call) else None
    except Exception:
        return None


def _key(call, row):
    if type(row) is not SearchResult:
        return None
    owner, provider, credential, account_id = call.coordinates
    if (type(row.source_provider) is not str or row.source_provider != provider
            or type(row.source_credential) is not str or row.source_credential != credential
            or type(row.source_account_id) not in (str, type(None))
            or row.source_account_id not in (None, "")
            or row.source_collection is not None
            or type(row.scenario) is not str
            or not _valid_coordinates((provider, row.record_id, row.docbase,
                                      row.medium, row.revision_id))
            or (row.docbase, row.scenario, row.medium, row.revision_id) not in call.branches):
        return None
    source = NoticeSourceKey(
        provider_id=provider, visibility="public", identity_id=None,
        credential_id=None, account_id=None, branch_id="search.public.catalogue",
        record_id="search:" + _coordinate_digest(provider, "public_catalog", row.record_id),
        edition_id="edition:" + _coordinate_digest(
            provider, "public_catalog", row.record_id, row.docbase, row.medium, row.revision_id,
        ),
        manifestation_role="digital",
    )
    return PublicBranchOccurrenceKey(owner, provider, credential, account_id, source)


def stamp_public_branch_borrow_occurrences(service, call, rows):
    """Retain only exact original rows, with ambiguous duplicates excluded."""
    if type(rows) not in (list, tuple):
        return
    for row in rows:
        if type(row) is SearchResult:
            row.__dict__.pop(_MARKER, None)
    if type(rows) is not list or len(rows) > 10000 or not _current(service, call):
        return
    try:
        pending = {}
        for row in rows:
            key = _key(call, row)
            if key is None:
                continue
            if key in pending:
                pending[key] = None
                continue
            fields = _fields(call.account, row)
            binding = _notice_binding(call.account, row)
            pending[key] = (_Receipt(_TOKEN, row, call, key, fields, binding)
                            if _valid_fields(fields) and binding is not None
                            and borrow_claim_key(call.account, row) is not None
                            else None)
        with source_context_mutation(service):
            inventory = getattr(service, "_native_borrow_occurrences", None)
            if type(inventory) is not dict:
                return
            for key, receipt in pending.items():
                inventory.pop(key, None)
                if receipt is not None:
                    receipt.row.__dict__[_MARKER] = receipt
                    inventory[key] = receipt.row
            while len(inventory) > 2048:
                inventory.pop(next(iter(inventory)))
    except Exception:
        for row in rows:
            if type(row) is SearchResult:
                row.__dict__.pop(_MARKER, None)


def stamp_public_branch_borrow_response(service, call, response):
    """Foreign public-only Search retains no row without same-call declaration.

    The caller continues to publish ONLY the existing sanitized projection.
    Keep the raw native occurrence within Core for a later explicit borrow.
    """
    from collections import Counter
    from ..plugin_api import ProviderSearchResponse, PublicSearchNotice
    from .public_search_projection import _key as projection_key, project_public_search_results

    if not _current(service, call) or type(response) is not ProviderSearchResponse:
        return
    try:
        projections = project_public_search_results(response)
        if projections is None or len({id(row) for row in response.results}) != len(response.results):
            return
        claims = Counter(
            (projection_key(decl.result), decl.result.revision_id)
            for decl in response.public_notices
            if type(decl) is PublicSearchNotice
            and type(decl.result) is SearchResult
            and projection_key(decl.result) is not None
            and type(decl.result.revision_id) in (str, type(None))
        )
        totals = Counter(k for (k, _revision) in claims.elements())
        rows = [row for row in response.results
                if id(row) in projections
                and _key(call, row) is not None
                and totals[projection_key(row)] == 1
                and claims[(projection_key(row), row.revision_id)] == 1]
        stamp_public_branch_borrow_occurrences(service, call, rows)
    except Exception:
        return


def public_branch_occurrence_document(service, row, *, owner):
    """A public source locator only, still NOT a verified member of visible C."""
    if type(row) is not SearchResult or type(owner) is not str:
        return None
    try:
        r = row.__dict__.get(_MARKER)
        if (type(r) is not _Receipt or r.token is not _TOKEN or r.row is not row
                or type(r.key) is not PublicBranchOccurrenceKey
                or any(type(v) is not str for v in (r.key.owner, r.key.provider, r.key.credential, r.key.account_id))
                or r.key.owner != owner or not _valid_key(r.key.source)
                or not _current(service, r.call) or r.call.scope.identity_id != owner
                or type(r.fields) is not tuple or not _valid_fields(r.fields)
                or type(r.binding) is not tuple or len(r.binding) != 3
                or any(type(v) not in (str, type(None)) for v in r.binding)):
            return None
        key = _key(r.call, row)
        if (key is None or key != r.key
                or _fields(r.call.account, row) != r.fields
                or _notice_binding(r.call.account, row) != r.binding
                or borrow_claim_key(r.call.account, row) is None):
            return None
        with source_context_mutation(service):
            inventory = getattr(service, "_native_borrow_occurrences", None)
            if type(inventory) is not dict or inventory.get(key) is not row:
                return None
        return notice_cover_document_ref(key.source)
    except Exception:
        return None


__all__ = ["PublicBranchOccurrenceKey", "capture_public_branch_borrow_call",
           "stamp_public_branch_borrow_occurrences", "stamp_public_branch_borrow_response",
           "public_branch_occurrence_document"]
