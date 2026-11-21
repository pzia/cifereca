"""#6397 — admission of stable authenticated links from the existing Search call.

Private source observations and their opaque self-C are written atomically.
No cross-source relation, session sharing, new GET or acquisition is inferred.
Only freshly admitted rows of the current owner's actual response are accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from sqlite3 import Error as SQLiteError
import time

from ..models import Account, SearchResult
from ..providers.base import ProviderDescriptor
from .loan_action_scope import _inert_session_accounts, _inert_session_state
from .notice_c_authenticated_web_choices import (
    FIELD, _Binding, _capture, _stable_url, _epoch,
)
from .notice_c_cover_index import (
    canonical_cover_document_ref, _persist_consolidated_cover_source_txn,
)
from .notice_cover_projection import notice_cover_document_ref, project_notice_cover_attribute
from .notice_cover_repository import _opaque_key, _persist_notice_cover_txn
from .notice_search_cover_producer import _coordinate_digest
from .notice_source_observation import NoticeSourceKey, admit_source_notice
from .source_context import source_context_mutation

_WITNESS = object()
_MARKER = "_core_private_web_search_receipt"
_COORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")


def _coordinate(value):
    return (type(value) is str and _COORD.fullmatch(value) is not None
            and "://" not in value and ".." not in value)


def _source_key(row, identity):
    if (type(row) is not SearchResult
            or not all(_coordinate(v) for v in (
                identity, row.source_provider, row.source_credential, row.source_account_id,
                row.record_id, row.docbase, row.scenario,
            ))
            or row.source_credential == "__public__"
            or type(row.medium) is not str or row.medium not in ("digital", "physical")
            or (row.revision_id is not None and not _coordinate(row.revision_id))
            or (row.source_collection is not None and not _coordinate(row.source_collection))):
        return None
    # Digest includes private account and identity: even a self-C's edition
    # coordinate must not reveal a title ID or imply equivalence across owners.
    return NoticeSourceKey(
        provider_id=row.source_provider, visibility="private", identity_id=identity,
        credential_id=row.source_credential, account_id=row.source_account_id,
        branch_id="search.authenticated.catalogue",
        record_id="search:" + _coordinate_digest(row.source_collection, row.record_id),
        edition_id="edition:" + _coordinate_digest(
            identity, row.source_provider, row.source_credential, row.source_account_id,
            row.source_collection, row.record_id, row.docbase, row.medium, row.revision_id,
        ),
        manifestation_role="digital" if row.medium == "digital" else "print",
    )


@dataclass(frozen=True, slots=True, repr=False)
class _Call:
    scope: object
    binding: _Binding
    observed_at: int


@dataclass(frozen=True, slots=True, repr=False)
class _Receipt:
    token: object
    row_id: int
    call: _Call
    key: NoticeSourceKey
    proof: tuple

    def __deepcopy__(self, memo):
        # Copying a DTO cannot copy Core/service locks or give the new row
        # a receipt for an observed response object it never belonged to.
        return self


def capture_authenticated_search_call(service, session, identity_id):
    """Capture once before an existing own-session provider call, never log in."""
    try:
        state = _inert_session_state(session)
        accounts = _inert_session_accounts(session)
        if state is None or accounts is None or len(accounts) != 1:
            return None
        descriptor = state.get("descriptor")
        account = accounts[0]
        if (type(descriptor) is not ProviderDescriptor
                or descriptor.capabilities.authentication is not True
                or type(account) is not Account or account.identity_id != identity_id
                or not all(_coordinate(v) for v in (
                    identity_id, account.provider_id, account.credential_id, account.unique_id,
                ))):
            return None
        from .search_orchestration import capture_search_read_scope
        scope = capture_search_read_scope(service, identity_id)
        placeholder = notice_cover_document_ref(NoticeSourceKey(
            account.provider_id, "private", identity_id, account.credential_id,
            account.unique_id, "search.authenticated.catalogue", "call", "call", "unknown",
        ))
        binding = _capture(service, scope, placeholder)
        if binding is None or binding.session is not session:
            return None
        return _Call(scope, binding, int(time.time()))
    except (AttributeError, TypeError, ValueError, LookupError, RuntimeError):
        return None


def _evidence(row, key, homepage):
    if (type(row.metadata) is not dict
            or row.metadata.get("catalogue_access") == "shared_read_only"
            or row.metadata.get("search_provenance") is not None
            or row.owned is not None and row.owned is not False
            or row.downloadable is not None and row.downloadable is not False
            or row.download_url is not None
            or not _stable_url(row.friendly_url, homepage)):
        return None
    raw = row.metadata.get("authenticated_web_evidence")
    expected = {
        "schema": 1, "provider_id": key.provider_id, "record_id": row.record_id,
        "docbase": row.docbase, "medium": row.medium, "source_revision": row.revision_id,
        "scenario": row.scenario, "access_mode": "authenticated",
    }
    if (type(raw) is not dict
            or set(raw) != set(expected) | {"evidence", "source_field"}
            or any(type(raw[k]) is not type(v) or raw[k] != v for k, v in expected.items())
            or type(raw["evidence"]) is not str or raw["evidence"] not in (
                key.provider_id + ".authenticated_search_record_identifier",
                key.provider_id + ".authenticated_search_product_card",
            )
            or not _coordinate(raw["source_field"])):
        return None
    return row.friendly_url, raw["evidence"], raw["source_field"]


def stamp_authenticated_search_rows(service, call, rows):
    """Only the exact returned objects get the transient Core observation receipt."""
    if type(rows) is not list or len(rows) > 10000:
        return
    for row in rows:
        if type(row) is SearchResult:
            row.__dict__.pop(_MARKER, None)
    if type(call) is not _Call or not call.binding.current(service, call.scope):
        return
    seen = {}
    for row in rows:
        key = _source_key(row, call.scope.identity_id)
        if key is not None:
            seen[key] = seen.get(key, 0) + 1
    for row in rows:
        key = _source_key(row, call.scope.identity_id)
        if key is None or seen[key] != 1:
            continue
        doc = notice_cover_document_ref(key)
        original = call.binding.document
        if (doc.provider_id, doc.credential_id, doc.account_id) != (
                original.provider_id, original.credential_id, original.account_id):
            continue
        proof = _evidence(row, key, call.binding.descriptor.homepage)
        if proof is not None:
            row.__dict__[_MARKER] = _Receipt(_WITNESS, id(row), call, key, proof)


def _prepare(service, row, identity):
    if type(row) is not SearchResult:
        return None
    receipt = row.__dict__.get(_MARKER)
    if (type(receipt) is not _Receipt or receipt.token is not _WITNESS
            or receipt.row_id != id(row) or type(receipt.call) is not _Call):
        return None
    call, key, proof = receipt.call, receipt.key, receipt.proof
    if (identity != call.scope.identity_id or _source_key(row, identity) != key
            or _evidence(row, key, call.binding.descriptor.homepage) != proof):
        return None
    doc = notice_cover_document_ref(key)
    if not call.binding.current(service, call.scope):
        return None
    observation = admit_source_notice(
        key=key, parser_id="core.search.authenticated-web", parser_version="1",
        observed_at=call.observed_at,
        raw_fields={"cover": None, "source_field": proof[2],
                    "source_record": {"id": row.record_id, "docbase": row.docbase,
                                      "medium": row.medium, "scenario": row.scenario,
                                      "revision": row.revision_id}, FIELD: {
            "schema": 1, "connection_epoch": call.binding.connection_epoch,
            "links": [{"url": proof[0], "evidence": proof[1]}],
        }},
    )
    return call, observation, doc


def _sql_binding_current(db, doc, epoch):
    # Same owner+borrower+generation snapshot as the write. No reentrant
    # storage connection or provider callbacks while the transaction is open.
    rows = db.execute(
        "SELECT c.connection_id,c.identity_id,c.enabled,e.epoch "
        "FROM provider_connections c JOIN provider_connection_epochs e "
        "ON e.provider_id=c.provider_id AND e.credential_id=c.credential_id "
        "WHERE c.provider_id=? AND c.credential_id=? LIMIT 2",
        (doc.provider_id, doc.credential_id),
    ).fetchall()
    if len(rows) != 1 or rows[0][1:] != (doc.identity_id, 1, epoch):
        return False
    return len(db.execute(
        "SELECT 1 FROM provider_accounts WHERE connection_id=? AND identity_id=? "
        "AND provider_id=? AND credential_id=? AND account_id=? LIMIT 2",
        (rows[0][0], doc.identity_id, doc.provider_id, doc.credential_id, doc.account_id),
    ).fetchall()) == 1


def persist_authenticated_search_rows(service, rows, *, identity_id):
    """Consume same-call receipts under the existing write fence, independently."""
    count = 0
    for row in rows:
        if type(row) is not SearchResult or type(row.__dict__.get(_MARKER)) is not _Receipt:
            continue
        try:
            with source_context_mutation(service):
                prepared = _prepare(service, row, identity_id)
                if prepared is None:
                    continue
                call, obs, doc = prepared
                attribute = project_notice_cover_attribute(
                    obs, field_path=("cover",), cover_inventory_complete=False,
                )
                storage = service.storage
                with storage._lock, storage._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if not _sql_binding_current(db, doc, call.binding.connection_epoch):
                        continue
                    source = _persist_notice_cover_txn(
                        db, obs, attribute, _opaque_key(doc), ("cover",), False, None,
                    )
                    if source is None or source.observation.source_revision != obs.source_revision:
                        continue
                    # Only exact private self identity: no link to public C or
                    # to another credential is fabricated by the URL or title.
                    _persist_consolidated_cover_source_txn(
                        db, canonical_edition_id=obs.key.edition_id, source=source,
                        relation_proof_id="private-self:" + _coordinate_digest(doc.source_key),
                    )
                    count += 1
        except (AttributeError, TypeError, ValueError, LookupError, RuntimeError, SQLiteError):
            # Exit the SQLite context before containing its failure: neither
            # a partial source nor a canonical sidecar may escape rollback.
            # Optional private documentary enrichment must not hide Search results.
            continue
    return count


def read_authenticated_web_for_search(service, row, *, reader_scope):
    """Exact private self-C lookup, including cached rows; never trust row URL."""
    from .notice_c_authenticated_web_choices import read_authenticated_web_choices
    from .search_orchestration import SearchReadScope
    if type(reader_scope) is not SearchReadScope or not reader_scope.current(service):
        return None
    key = _source_key(row, reader_scope.identity_id)
    if key is None:
        return None
    return read_authenticated_web_choices(
        service, canonical_cover_document_ref(key.edition_id),
        reader_scope=reader_scope, source_documents=(notice_cover_document_ref(key),),
    )


__all__ = ["capture_authenticated_search_call", "stamp_authenticated_search_rows",
           "persist_authenticated_search_rows", "read_authenticated_web_for_search"]
