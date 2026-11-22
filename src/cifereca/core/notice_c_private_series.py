"""#1078: credential-scoped series from exact authenticated Search.

No borrower inference, web locator, public declaration or provider dispatch.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import time
from sqlite3 import Error as SQLiteError

from ..models import SearchResult
from ..providers.base import ProviderCapabilities, ProviderDescriptor
from .loan_action_scope import _inert_session_state
from .notice_c_authenticated_web_choices import _native_reader_scope, _capture
from .notice_c_authenticated_web_retention import _scope_digest
from .notice_c_private_search_web import _coordinate, _sql_binding_current
from .notice_cover_projection import notice_cover_document_ref, project_notice_cover_attribute
from .notice_cover_repository import (
    _checked_source_state, _opaque_key, _persist_notice_cover_txn,
    _source_fingerprint, _unique_object,
)
from .notice_search_cover_producer import _coordinate_digest
from .notice_source_observation import NoticeSourceKey, _valid_key, admit_source_notice
from .public_search_projection import declared_public_series_relation, decode_public_series_relation
from .source_context import source_context_mutation

_BRANCH = "search.authenticated.catalogue.series"
_PARSER = "core.search.authenticated-series"
_FIELD = "series_relation"
_MARKER = "_core_private_series_receipt"
_PREFIX = "notice.c.series.private.v1:"
_WITNESS = object()
_FIELDS = ("label", "source", "source_field", "scope", "confidence", "freshness")


def _epoch(value):
    return type(value) is int and 1 <= value < 2**63


def _generation(value):
    return (value is None or type(value) is int and 0 <= value < 2**63
            or type(value) is str and 0 < len(value) <= 128 and value.isprintable())


@dataclass(frozen=True, slots=True, repr=False)
class _Call:
    scope: object
    session: object
    descriptor: ProviderDescriptor
    provider: str
    credential: str
    epoch: int
    generation: object
    observed_at: int


@dataclass(frozen=True, slots=True, repr=False)
class _Receipt:
    token: object
    row_id: int
    call: _Call
    key: NoticeSourceKey
    proof: tuple

    def __deepcopy__(self, memo):
        return self


def _current(service, call):
    if (type(call) is not _Call
            or not _native_reader_scope(call.scope, service)
            or not _coordinate(call.provider) or not _coordinate(call.credential)
            or call.credential == "__public__"
            or not _epoch(call.epoch) or not _epoch(call.observed_at)
            or not _generation(call.generation)
            or type(call.descriptor) is not ProviderDescriptor):
        return False
    try:
        state = _inert_session_state(call.session)
        descriptor = call.descriptor
        return (
            state is not None and state.get("descriptor") is descriptor
            and type(descriptor.id) is str and descriptor.id == call.provider
            and type(descriptor.state) is str and descriptor.state == "ready"
            and type(descriptor.capabilities) is ProviderCapabilities
            and descriptor.capabilities.authentication is True
            and type(state.get("provider_id")) is str
            and state["provider_id"] == call.provider
            and type(state.get("credential_id")) is str
            and state["credential_id"] == call.credential
            and state.get("connected") is True and state.get("_closed") is not True
            and type(state.get("_epoch")) is type(call.generation)
            and state.get("_epoch") == call.generation
            and service._sessions.get(service._session_key(call.provider, call.credential)) is call.session
            and call.scope.current(service)
            and service._provider_enabled_for_identity(call.provider, call.scope.identity_id) is True
            and service.storage.provider_connection_epoch_current(
                call.provider, call.credential, call.scope.identity_id, call.epoch,
            ) is True
            and call.scope.current(service)
        )
    except Exception:
        return False


def capture_series_call(service, session, identity_id):
    """Pin the owner connection and session before the existing Search."""
    try:
        from .search_orchestration import capture_search_read_scope
        state = _inert_session_state(session)
        if state is None or not _coordinate(identity_id):
            return None
        provider, credential = state.get("provider_id"), state.get("credential_id")
        if not _coordinate(provider) or not _coordinate(credential) or credential == "__public__":
            return None
        descriptor = state.get("descriptor")
        if (type(descriptor) is not ProviderDescriptor
                or type(descriptor.capabilities) is not ProviderCapabilities
                or descriptor.capabilities.authentication is not True):
            return None
        scope = capture_search_read_scope(service, identity_id)
        epoch = service.storage.provider_connection_epoch(provider, credential, identity_id)
        call = _Call(scope, session, descriptor, provider, credential, epoch,
                     state.get("_epoch"), int(time.time()))
        return call if _current(service, call) else None
    except Exception:
        return None


def _key(row, identity):
    # Preserve the original credential-only key. An explicitly observed
    # account uses a distinct key and must pass the native borrower binding.
    if (type(row) is not SearchResult
            or (row.source_account_id is not None and not _coordinate(row.source_account_id))
            or not all(_coordinate(x) for x in (
                identity, row.source_provider, row.source_credential,
                row.record_id, row.docbase, row.scenario,
            )) or row.source_credential == "__public__"
            or type(row.medium) is not str or row.medium not in ("digital", "physical")
            or (row.source_collection is not None and not _coordinate(row.source_collection))
            or (row.revision_id is not None and not _coordinate(row.revision_id))):
        return None
    edition = (
        identity, row.source_provider, row.source_credential, row.source_collection,
        row.record_id, row.docbase, row.medium, row.revision_id,
    )
    if row.source_account_id is not None:
        edition += ("account", row.source_account_id)
    return NoticeSourceKey(
        row.source_provider, "private", identity, row.source_credential,
        row.source_account_id, _BRANCH,
        "search:" + _coordinate_digest(row.source_collection, row.record_id),
        "edition:" + _coordinate_digest(*edition),
        "digital" if row.medium == "digital" else "print",
    )


def _proof(row):
    metadata = row.metadata
    if (type(metadata) is not dict or len(metadata) > 256
            or any(type(k) is not str for k in metadata)
            or metadata.get("search_provenance") is not None
            or (metadata.get("catalogue_access") is not None
                and type(metadata["catalogue_access"]) is not str)
            or metadata.get("catalogue_access") == "shared_read_only"
            or row.owned is not None and row.owned is not False
            or row.downloadable is not None and row.downloadable is not False
            or row.download_url is not None):
        return None
    relation = declared_public_series_relation(
        metadata, provider_id=row.source_provider, scenario=row.scenario,
    )
    return tuple(getattr(relation, k) for k in _FIELDS) if relation is not None else None


def _account_current(service, call, key):
    if key.account_id is None:
        return True
    # Same registered account and live native source, not a guessed primary
    # account or the navigation profile. Reuse the existing private binding.
    binding = _capture(service, call.scope, notice_cover_document_ref(key))
    return (binding is not None and binding.session is call.session
            and binding.descriptor is call.descriptor
            and binding.connection_epoch == call.epoch)


def stamp_series_rows(service, call, rows):
    if type(rows) not in (list, tuple):
        return
    for row in rows:
        if type(row) is SearchResult:
            row.__dict__.pop(_MARKER, None)
    if type(rows) is not list or len(rows) > 10000 or not _current(service, call):
        return
    seen = {}
    for row in rows:
        key = _key(row, call.scope.identity_id)
        if key is not None:
            seen[key] = seen.get(key, 0) + 1
    for row in rows:
        key = _key(row, call.scope.identity_id)
        if (key is None or seen[key] != 1
                or (key.provider_id, key.credential_id) != (call.provider, call.credential)
                or not _account_current(service, call, key)):
            continue
        proof = _proof(row)
        if proof is not None:
            row.__dict__[_MARKER] = _Receipt(_WITNESS, id(row), call, key, proof)


def _sql_current(db, doc, epoch):
    if doc.account_id is not None:
        return _sql_binding_current(db, doc, epoch)
    return db.execute(
        "SELECT c.identity_id,c.enabled,e.epoch FROM provider_connections c "
        "JOIN provider_connection_epochs e ON e.provider_id=c.provider_id "
        "AND e.credential_id=c.credential_id WHERE c.provider_id=? "
        "AND c.credential_id=? LIMIT 2", (doc.provider_id, doc.credential_id),
    ).fetchall() == [(doc.identity_id, 1, epoch)]


def persist_series_rows(service, rows, *, identity_id):
    if type(rows) is not list or len(rows) > 10000 or not _coordinate(identity_id):
        return 0
    from .notice_c_cover_index import _persist_consolidated_cover_source_txn
    count = 0
    for row in rows:
        if type(row) is not SearchResult:
            continue
        receipt = row.__dict__.get(_MARKER)
        if (type(receipt) is not _Receipt or receipt.token is not _WITNESS
                or type(receipt.row_id) is not int or receipt.row_id != id(row)
                or type(receipt.key) is not NoticeSourceKey or not _valid_key(receipt.key)
                or type(receipt.proof) is not tuple or len(receipt.proof) != 6
                or any(type(x) is not str or len(x) > 240 for x in receipt.proof)):
            continue
        try:
            with source_context_mutation(service):
                call = receipt.call
                if (not _current(service, call) or call.scope.identity_id != identity_id
                        or _key(row, identity_id) != receipt.key
                        or _proof(row) != receipt.proof
                        or (receipt.key.provider_id, receipt.key.credential_id)
                        != (call.provider, call.credential)
                        or not _account_current(service, call, receipt.key)):
                    continue
                obs = admit_source_notice(
                    key=receipt.key, parser_id=_PARSER, parser_version="1",
                    observed_at=call.observed_at,
                    raw_fields={"cover": None, "connection_epoch": call.epoch,
                                _FIELD: dict(zip(_FIELDS, receipt.proof))},
                )
                doc = notice_cover_document_ref(obs.key)
                attr = project_notice_cover_attribute(
                    obs, field_path=("cover",), cover_inventory_complete=False,
                )
                storage = service.storage
                with storage._lock:
                    db = storage._connect()
                    if db.in_transaction:
                        continue
                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        if not _sql_current(db, doc, call.epoch):
                            continue
                        source = _persist_notice_cover_txn(
                            db, obs, attr, _opaque_key(doc), ("cover",), False, None,
                        )
                        if source is None or source.observation.source_revision != obs.source_revision:
                            continue
                        _persist_consolidated_cover_source_txn(
                            db, canonical_edition_id=obs.key.edition_id, source=source,
                            relation_proof_id="private-self:" + _coordinate_digest(doc.source_key),
                        )
            count += 1
        except (AttributeError, TypeError, ValueError, LookupError, RuntimeError, SQLiteError):
            continue
    return count


def _retained_key(doc):
    return _PREFIX + doc.source_key.removeprefix("notice-c:v1:")


def retain_series_txn(db, source, original):
    """Retain the exact source field in the canonical writer's transaction."""
    obs, doc = source.observation, source.document
    if obs.key.visibility != "private" or obs.key.branch_id != _BRANCH:
        return None
    raw = obs.fields_copy()
    relation = decode_public_series_relation(raw.get(_FIELD), provider_id=doc.provider_id)
    if (obs.parser_id != _PARSER or obs.parser_version != "1"
            or obs.status != "complete"
            or not _epoch(raw.get("connection_epoch")) or relation is None):
        db.execute("DELETE FROM metadata WHERE key=?", (_retained_key(doc),))
        return None
    payload = {
        "schema": 1, "scope": _scope_digest(doc),
        "source_epoch": source.epoch, "source_revision": obs.source_revision,
        "proof": _source_fingerprint(obs, tuple(original["field_path"]),
                                     original["cover_inventory_complete"]),
        "connection_epoch": raw["connection_epoch"], _FIELD: asdict(relation),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    db.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) "
        "DO UPDATE SET value=excluded.value WHERE value<>excluded.value",
        (_retained_key(doc), encoded),
    )
    return sha256(encoded.encode("ascii")).hexdigest()


def read_series_for_search(service, row, *, reader_scope):
    """Exact source-context read after eviction/restart, no live session or GET."""
    from .notice_c_cover_index import canonical_cover_document_ref, _db_key, _decode
    if not _native_reader_scope(reader_scope, service) or not reader_scope.current(service):
        return None
    key = _key(row, reader_scope.identity_id)
    if key is None:
        return None
    try:
        # A durable enabled connection is necessary, but not sufficient:
        # disabling the provider must also revoke offline presentation.
        if service._provider_enabled_for_identity(key.provider_id, key.identity_id) is not True:
            return None
        scenario = row.scenario
        doc = notice_cover_document_ref(key)
        canonical = canonical_cover_document_ref(key.edition_id)
        storage = service.storage
        with storage._lock:
            db = storage._connect()
            if db.in_transaction:
                return None
            with db:
                db.execute("BEGIN")
                saved = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(canonical),)).fetchone()
                if saved is None:
                    return None
                member = next((m for m in _decode(saved[0], canonical).sources
                               if m.source_ref == doc.source_key and m.visibility == "private"
                               and m.provider_id == doc.provider_id), None)
                if member is None or member.private_series_digest is None:
                    return None
                _, source, fence = _checked_source_state(db, doc, _opaque_key(doc))
                if fence is None or (source is None and (
                        fence.get("evicted") is not True or fence["epoch"] != member.source_epoch + 1
                    )) or (source is not None and (
                        source.epoch != member.source_epoch
                        or source.observation.source_revision != member.source_revision
                    )):
                    return None
                retained = db.execute("SELECT value FROM metadata WHERE key=?", (_retained_key(doc),)).fetchone()
                if retained is None or type(retained[0]) is not str or len(retained[0]) > 8192:
                    return None
                if sha256(retained[0].encode("ascii")).hexdigest() != member.private_series_digest:
                    return None
                raw = json.loads(retained[0], object_pairs_hook=_unique_object)
                if (type(raw) is not dict or set(raw) != {
                        "schema", "scope", "source_epoch", "source_revision", "proof",
                        "connection_epoch", _FIELD,
                    } or type(raw["schema"]) is not int or raw["schema"] != 1
                    or raw["scope"] != _scope_digest(doc)
                    or not _epoch(raw["source_epoch"]) or raw["source_epoch"] != member.source_epoch
                    or raw["source_revision"] != member.source_revision
                    or raw["proof"] != fence["proof"] or not _epoch(raw["connection_epoch"])
                    or not _sql_current(db, doc, raw["connection_epoch"])):
                    return None
                relation = decode_public_series_relation(
                    raw[_FIELD], provider_id=doc.provider_id, scenario=scenario,
                )
        # Recheck policy and scope after the snapshot. A row reused under
        # another source/scenario must not inherit this result.
        if (not reader_scope.current(service)
                or service._provider_enabled_for_identity(key.provider_id, key.identity_id) is not True
                or _key(row, reader_scope.identity_id) != key
                or row.scenario != scenario
                or not reader_scope.current(service)):
            return None
        return relation
    except (AttributeError, TypeError, ValueError, LookupError, RuntimeError, SQLiteError):
        return None
