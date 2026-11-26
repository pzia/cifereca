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
_PARSE_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PARSE_FIELDS = ("title", "author", "publisher")
_PARSE_MARKER = "_core_private_notice_parse_input"


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
    parse_input: tuple | None = None

    def __deepcopy__(self, memo):
        # Copying a DTO cannot copy Core/service locks or give the new row
        # a receipt for an observed response object it never belonged to.
        return self


@dataclass(frozen=True, slots=True, repr=False)
class _BatchCall:
    calls: tuple[_Call, ...]


def capture_authenticated_search_call(service, session, identity_id):
    """Pin registered owner accounts before existing Search; never log in."""
    try:
        state = _inert_session_state(session)
        accounts = _inert_session_accounts(session)
        if state is None or accounts is None or not 1 <= len(accounts) <= 64:
            return None
        descriptor = state.get("descriptor")
        if (type(descriptor) is not ProviderDescriptor
                or descriptor.capabilities.authentication is not True
                or not _coordinate(identity_id)):
            return None
        from .search_orchestration import capture_search_read_scope
        scope = capture_search_read_scope(service, identity_id)
        observed_at = int(time.time())
        calls = []
        for account in accounts:
            if (type(account) is not Account or account.identity_id != identity_id
                    or account.provider_id != state.get("provider_id")
                    or account.credential_id != state.get("credential_id")
                    or not all(_coordinate(v) for v in (
                        identity_id, account.provider_id, account.credential_id, account.unique_id,
                    ))):
                return None
            placeholder = notice_cover_document_ref(NoticeSourceKey(
                account.provider_id, "private", identity_id, account.credential_id,
                account.unique_id, "search.authenticated.catalogue", "call", "call", "unknown",
            ))
            binding = _capture(service, scope, placeholder)
            if binding is not None and binding.session is session and binding.account is account:
                calls.append(_Call(scope, binding, observed_at))
        if not calls:
            return None
        return calls[0] if len(calls) == 1 else _BatchCall(tuple(calls))
    except (AttributeError, TypeError, ValueError, LookupError, RuntimeError):
        return None


def _evidence(row, key, homepage):
    metadata = row.metadata
    # Evidence is a fixed native parser declaration, not an extensible
    # provider dictionary. Refuse non-string keys before any .get() that
    # could invoke key equality on an untrusted mapping.
    if (type(metadata) is not dict or len(metadata) > 256
            or any(type(name) is not str for name in metadata)
            or (metadata.get("catalogue_access") is not None
                and type(metadata["catalogue_access"]) is not str)
            or metadata.get("catalogue_access") == "shared_read_only"
            or metadata.get("search_provenance") is not None
            or row.owned is not None and row.owned is not False
            or row.downloadable is not None and row.downloadable is not False
            or row.download_url is not None
            or not _stable_url(row.friendly_url, homepage)):
        return None
    raw = metadata.get("authenticated_web_evidence")
    expected = {
        "schema": 1, "provider_id": key.provider_id, "record_id": row.record_id,
        "docbase": row.docbase, "medium": row.medium, "source_revision": row.revision_id,
        "scenario": row.scenario, "access_mode": "authenticated",
    }
    if (type(raw) is not dict or len(raw) != len(expected) + 2
            or any(type(name) is not str for name in raw)
            or set(raw) != set(expected) | {"evidence", "source_field"}
            or any(type(raw[k]) is not type(v) or raw[k] != v for k, v in expected.items())
            or type(raw["evidence"]) is not str or raw["evidence"] not in (
                key.provider_id + ".authenticated_search_record_identifier",
                key.provider_id + ".authenticated_search_product_card",
            )
            or not _coordinate(raw["source_field"])):
        return None
    return row.friendly_url, raw["evidence"], raw["source_field"]


def _parse_values(row):
    values = tuple(getattr(row, name, None) for name in _PARSE_FIELDS)
    if any(value is not None and (type(value) is not str or len(value) > 4096)
           for value in values):
        return None
    return values


def _valid_parse_input(packet, row):
    if (type(packet) is not tuple or len(packet) != 4
            or any(type(value) is not str or _PARSE_SHA.fullmatch(value) is None
                   for value in packet[:2])
            or type(packet[2]) is not tuple or not 1 <= len(packet[2]) <= len(_PARSE_FIELDS)
            or any(type(name) is not str or name not in _PARSE_FIELDS for name in packet[2])
            or len(set(packet[2])) != len(packet[2])
            or type(packet[3]) is not tuple or len(packet[3]) != len(_PARSE_FIELDS)
            or any(value is not None and (type(value) is not str or len(value) > 4096)
                   for value in packet[3])):
        return False
    values = _parse_values(row)
    return (values is not None and values == packet[3]
            and all(type(values[_PARSE_FIELDS.index(name)]) is str
                    and values[_PARSE_FIELDS.index(name)] for name in packet[2]))


def _same_call_parse_input(call, row):
    """The already bound producer may attest its exact native response, not a DTO hash."""
    try:
        read = getattr(call.binding.session, "private_search_parse_evidence", None)
        declaration = read(row) if callable(read) else None
        if type(declaration) is not tuple or len(declaration) != 3:
            return None
        packet = (*declaration, _parse_values(row))
        return packet if _valid_parse_input(packet, row) else None
    except (AttributeError, TypeError, ValueError, RuntimeError, OSError):
        return None


def stamp_authenticated_search_rows(service, call, rows):
    """Only the exact returned objects get the transient Core observation receipt."""
    if type(rows) not in (list, tuple):
        return
    # Invalidate receipts BEFORE admission and its 10k budget check.
    # A reused row in an oversized or wrong-container response must not
    # keep an older call's private publication authority. Even rejected
    # materialized batches are cleared (persistence already walks each row).
    for row in rows:
        if type(row) is SearchResult:
            row.__dict__.pop(_MARKER, None)
            row.__dict__.pop(_PARSE_MARKER, None)
    if type(rows) is not list or len(rows) > 10000:
        return
    calls = (call,) if type(call) is _Call else call.calls if type(call) is _BatchCall else ()
    by_account = {}
    for captured in calls:
        if type(captured) is not _Call or not captured.binding.current(service, captured.scope):
            continue
        doc = captured.binding.document
        coords = (doc.provider_id, doc.credential_id, doc.account_id)
        by_account[coords] = captured if coords not in by_account else None
    seen = {}
    prepared = []
    for row in rows:
        if type(row) is not SearchResult or not all(_coordinate(v) for v in (
                row.source_provider, row.source_credential, row.source_account_id)):
            continue
        captured = by_account.get((row.source_provider, row.source_credential, row.source_account_id))
        if captured is None:
            continue
        key = _source_key(row, captured.scope.identity_id)
        if key is not None:
            seen[key] = seen.get(key, 0) + 1
            prepared.append((row, captured, key))
    for row, captured, key in prepared:
        if seen[key] != 1:
            continue
        proof = _evidence(row, key, captured.binding.descriptor.homepage)
        if proof is not None:
            row.__dict__[_MARKER] = _Receipt(
                _WITNESS, id(row), captured, key, proof,
                _same_call_parse_input(captured, row),
            )


def _prepare(service, row, identity):
    if type(row) is not SearchResult:
        return None
    receipt = row.__dict__.get(_MARKER)
    if (type(receipt) is not _Receipt or receipt.token is not _WITNESS
            or type(receipt.row_id) is not int or receipt.row_id != id(row)
            or type(receipt.call) is not _Call):
        return None
    call, key, proof = receipt.call, receipt.key, receipt.proof
    # These packet objects are Core-issued, but an opaque dataclass/tuple
    # can still be substituted on a mutable SearchResult before the writer.
    # Never call a foreign equality, .current() or provider descriptor.
    from .search_orchestration import SearchReadScope
    from .notice_source_observation import _valid_key
    if (type(identity) is not str or not 0 < len(identity) <= 1024
            or type(call.scope) is not SearchReadScope
            or call.scope.service is not service
            or type(call.scope.identity_id) is not str
            or not 0 < len(call.scope.identity_id) <= 1024
            or type(call.scope.source_epoch) is not int
            or call.scope.source_epoch < 0
            or type(call.binding) is not _Binding
            or type(call.binding.descriptor) is not ProviderDescriptor
            or type(call.observed_at) is not int
            or not 1 <= call.observed_at <= 2**63 - 1
            or type(key) is not NoticeSourceKey or not _valid_key(key)
            or type(proof) is not tuple or len(proof) != 3
            or any(type(value) is not str or len(value) > 512
                   for value in proof)):
        return None
    if (identity != call.scope.identity_id or _source_key(row, identity) != key
            or _evidence(row, key, call.binding.descriptor.homepage) != proof):
        return None
    doc = notice_cover_document_ref(key)
    original = call.binding.document
    if ((doc.identity_id, doc.provider_id, doc.credential_id, doc.account_id)
            != (original.identity_id, original.provider_id, original.credential_id, original.account_id)
            or not call.binding.current(service, call.scope)):
        return None
    packet = receipt.parse_input
    if packet is not None and not _valid_parse_input(packet, row):
        return None
    bibliography = ({"private_bibliography": dict(zip(_PARSE_FIELDS, packet[3]))}
                    if packet is not None else {})
    observation = admit_source_notice(
        key=key, parser_id="core.search.authenticated-web", parser_version="1",
        observed_at=call.observed_at,
        raw_fields={**bibliography, "cover": None, "source_field": proof[2],
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
    # The write port must accept exactly the stamped, bounded response shape.
    # A generator/tuple can replay old receipts outside the call's batch;
    # an oversized batch cannot bypass stamp's all-or-nothing admission.
    if (type(rows) is not list or len(rows) > 10000
            or type(identity_id) is not str
            or not 0 < len(identity_id) <= 1024):
        return 0
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
                row.__dict__.pop(_PARSE_MARKER, None)
                packet = row.__dict__[_MARKER].parse_input
                diagnostic = None
                rejected = False
                with storage._lock, storage._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if not _sql_binding_current(db, doc, call.binding.connection_epoch):
                        continue
                    from .notice_cover_repository import _checked_source_state
                    from .notice_parse_witness import admitted_source_scope_sha256
                    from .notice_parse_witness_store import _PREFIX
                    from .notice_parse_witness_alert import review_publication_txn
                    _, previous, fence = _checked_source_state(db, doc, _opaque_key(doc))
                    if (previous is not None and obs.observed_at < previous.observation.observed_at
                            or fence is not None and obs.observed_at < fence["last_seen_at"]):
                        continue
                    witness_scope = admitted_source_scope_sha256(obs.key, parser_id=obs.parser_id)
                    pinned = db.execute("SELECT 1 FROM metadata WHERE key=?",
                                        (_PREFIX + witness_scope,)).fetchone()
                    if pinned is not None:
                        if packet is None:
                            # Never overwrite a pinned source from a weaker,
                            # unattested private parser path.
                            continue
                        rejected, diagnostic = review_publication_txn(
                            db, current=previous.observation if previous is not None else None,
                            candidate=obs, input_digest=packet[0], implementation_digest=packet[1],
                        )
                    if rejected:
                        source = previous
                    else:
                        source = _persist_notice_cover_txn(
                            db, obs, attribute, _opaque_key(doc), ("cover",), False, None,
                        )
                        if source is None or source.observation.source_revision != obs.source_revision:
                            continue
                        # Only exact private self identity: no link to public C or
                        # another credential is fabricated by the URL or title.
                        _persist_consolidated_cover_source_txn(
                            db, canonical_edition_id=obs.key.edition_id, source=source,
                            relation_proof_id="private-self:" + _coordinate_digest(doc.source_key),
                        )
            if packet is not None and source is not None:
                row.__dict__[_PARSE_MARKER] = (
                    _WITNESS, source.epoch, obs.source_revision, packet[0], packet[1],
                )
            if rejected:
                # Capture is deliberately partial for private inputs. Only
                # sanitized technical metadata is queued by the shared gate.
                _export_private_witness_capture(service, diagnostic)
                continue
            # Account only after both the SQLite transaction and Core's
            # mutation context completed. A failed commit must never be
            # reported as one durable private C publication.
            count += 1
        except (AttributeError, TypeError, ValueError, LookupError, RuntimeError, SQLiteError):
            # Exit the SQLite context before containing its failure: neither
            # a partial source nor a canonical sidecar may escape rollback.
            # Optional private documentary enrichment must not hide Search results.
            continue
    return count


def _export_private_witness_capture(service, diagnostic):
    """Shared post-commit private capture: no private body or values exported."""
    if diagnostic is None:
        return
    from .notice_regression_capture_intent import export_capture
    from .notice_parse_witness_alert import report_capture_diagnostic
    try:
        state = export_capture(
            service.storage, diagnostic, members={},
            directory=getattr(service, "_notice_regression_capture_directory", None),
        )
    except (ValueError, OSError, SQLiteError):
        state = "metadata_unavailable"
    if state is not None:
        report_capture_diagnostic(getattr(service, "diagnostics", None),
                                  diagnostic, state, rejected=True)


def read_authenticated_web_for_search(service, row, *, reader_scope):
    """Exact private self-C lookup, including cached rows; never trust row URL."""
    from .notice_c_authenticated_web_choices import read_authenticated_web_choices
    from .search_orchestration import SearchReadScope
    if type(reader_scope) is not SearchReadScope or not reader_scope.current(service):
        return None
    # Shared catalogue projections are deliberately inert. A retained row
    # from a foreign profile must not acquire private web authority merely
    # because navigation later returns to its credential's owner.
    if type(row) is not SearchResult:
        return None
    metadata = row.metadata
    if (type(metadata) is not dict or len(metadata) > 256
            or any(type(name) is not str for name in metadata)):
        return None
    access = metadata.get("catalogue_access")
    provenance = metadata.get("search_provenance")
    if (type(access) not in (str, type(None))
            or type(provenance) not in (str, type(None))
            or access == "shared_read_only"
            or provenance == "known_catalog_stale"):
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
