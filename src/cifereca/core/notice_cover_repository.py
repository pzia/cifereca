"""#2385/#4552: durable, exact source observations for Notice C cover read-through.

The canonical data is the *admitted raw NoticeSourceObservation* plus the
parser's declared cover field contract. There is no separate image/book table.
The existing SQLite metadata primary-key index is keyed by an opaque exact
source/edition digest, never by the viewing profile, title, ISBN or media URL.

All writes originate in an already-authorized Core producer. This module
cannot grant provider access, invent a parsed source, download image bytes,
reopen a file or infer the mapping between distinct books.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import sqlite3
import json
import re
from typing import Any

from .book_cover_projection import CoverDocumentScope
from .notice_cover_projection import (
    NoticeCoverAttribute, notice_cover_document_ref,
    project_notice_cover_attribute,
)
from .notice_source_observation import NoticeSourceKey, NoticeSourceObservation

_PREFIX = "notice.c.observation.v1:"
# A compact per-source fencing record survives short observation eviction.
# No bibliography, URL, account data or duplicate notice body is stored.
_FENCE_PREFIX = "notice.c.source-fence.v1:"
_MAX_BYTES = 131072
_MAX_EPOCH = 2**63 - 1
_SOURCE_FIELDS = frozenset((
    "provider_id", "visibility", "identity_id", "credential_id",
    "account_id", "branch_id", "record_id", "edition_id",
    "manifestation_role",
))
_ROW_FIELDS = frozenset((
    "schema", "key", "parser_id", "parser_version", "observed_at",
    "status", "source_revision", "raw_json", "field_path",
    "cover_inventory_complete", "epoch",
))
# Optional watermark allows readers to replay pre-upgrade schema-1 records.
# This tracks source freshness, NOT a second documentary revision or N2 epoch.
_LAST_SEEN = "last_seen_at"
# Private documentary bytes shared ONLY by independently admitted native
# resource observations. Owner/account scopes and revision fences stay exact.
_SHARED_PREFIX = "notice.c.private-documentary.v1:"
_SHARED_FIELD = "documentary_payload"
_SHARED_ROW_FIELDS = (_ROW_FIELDS - {"raw_json"}) | {_SHARED_FIELD}
_SHARED_EDITION = re.compile(r"edition:[0-9a-f]{64}\Z")
_SHARED_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_SHARED_REFS = 512
_EXACT_SOURCE = re.compile(r"notice-c:v1:[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class PersistedNoticeCover:
    """An admitted Notice C observation and its monotone, derived N2 epoch."""
    observation: NoticeSourceObservation
    attribute: NoticeCoverAttribute
    epoch: int

    @property
    def document(self) -> CoverDocumentScope:
        return self.attribute.document


def _opaque_key(document: CoverDocumentScope) -> str | None:
    if type(document) is not CoverDocumentScope:
        raise TypeError("expected exact Notice C DocumentRef")
    # A dataclass frozen at construction can still be altered by a caller.
    # The C-source key is a read/write trust boundary shared by N2, Search
    # and exact web-link lookup. Reject non-native members *before* regex,
    # comparison or database binding; never invoke a foreign __eq__.
    for name in ("kind", "source_key", "provider_id", "credential_id",
                 "loan_id", "artifact_id", "placement_id"):
        value = getattr(document, name)
        if type(value) is not str or len(value) > 512 or not value.isprintable():
            return None
    if (document.identity_id is not None
            and (type(document.identity_id) is not str
                 or not 0 < len(document.identity_id) <= 512
                 or not document.identity_id.isprintable())):
        return None
    if (document.account_id is not None
            and (type(document.account_id) is not str
                 or len(document.account_id) > 512
                 or not document.account_id.isprintable())):
        return None
    if (document.kind not in ("catalogue", "public_catalogue")
            or not document.provider_id
            or document.loan_id or document.artifact_id or document.placement_id
            or _EXACT_SOURCE.fullmatch(document.source_key) is None):
        return None
    if document.kind == "public_catalogue":
        if (document.identity_id is not None or document.account_id is not None
                or document.credential_id != "__public__"):
            return None
    elif (document.identity_id is None or not document.credential_id
          or document.credential_id == "__public__"):
        return None
    return _PREFIX + document.source_key[len("notice-c:v1:"):]


def is_notice_cover_document(document: CoverDocumentScope) -> bool:
    """Pure eligibility for exact-source or explicit canonical C read-through."""
    if type(document) is not CoverDocumentScope:
        return False
    if _opaque_key(document) is not None:
        return True
    # The canonical index depends on this repository for source revalidation.
    # Delay the import to avoid a module cycle; the predicate does no I/O.
    from .notice_c_cover_index import is_canonical_cover_document
    return is_canonical_cover_document(document)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("notice_c_persisted_corrupt")
        result[name] = value
    return result


def _documentary_key(provider, edition, body):
    payload = json.dumps([provider, edition, body], ensure_ascii=True,
                         separators=(",", ":"))
    return _SHARED_PREFIX + sha256(payload.encode("ascii")).hexdigest()


def _shared_pointer(row):
    pointer = row.get(_SHARED_FIELD)
    if (type(pointer) is not dict or set(pointer) != {"edition", "digest", "private_web"}
            or type(pointer["edition"]) is not str
            or _SHARED_EDITION.fullmatch(pointer["edition"]) is None
            or type(pointer["digest"]) is not str
            or _SHARED_HASH.fullmatch(pointer["digest"]) is None
            or type(pointer["private_web"]) is not dict):
        raise ValueError("notice_private_documentary_pointer_invalid")
    return pointer


def _shared_payload(db, row, *, source_ref, adding=False):
    pointer = _shared_pointer(row)
    if db is None:
        raise ValueError("notice_private_documentary_snapshot_required")
    provider = row["key"]["provider_id"]
    key = _SHARED_PREFIX + pointer["digest"]
    saved = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    if saved is None or type(saved[0]) is not str or len(saved[0].encode("utf-8")) > _MAX_BYTES:
        raise ValueError("notice_private_documentary_missing")
    payload = json.loads(saved[0], object_pairs_hook=_unique_object)
    if (type(payload) is not dict or set(payload) != {"body", "refs"}
            or type(payload["body"]) is not str
            or len(payload["body"].encode("utf-8")) > 65536
            or type(payload["refs"]) is not list
            or not 1 <= len(payload["refs"]) <= _MAX_SHARED_REFS
            or any(type(ref) is not str or _EXACT_SOURCE.fullmatch(ref) is None
                   for ref in payload["refs"])
            or payload["refs"] != sorted(set(payload["refs"]))
            or type(source_ref) is not str or _EXACT_SOURCE.fullmatch(source_ref) is None
            or (source_ref in payload["refs"]) == adding
            or _documentary_key(provider, pointer["edition"], payload["body"]) != key):
        raise ValueError("notice_private_documentary_corrupt")
    return key, payload


def _shared_raw_json(db, row, key):
    # No public reader or arbitrary private parser may consume this pointer.
    if (key.visibility != "private" or key.branch_id != "search.authenticated.catalogue"
            or row["parser_id"] != "core.search.authenticated-web"
            or row["parser_version"] != "1" or row["status"] != "complete"
            or row["field_path"] != ["cover"] or row["cover_inventory_complete"] is not False):
        raise ValueError("notice_private_documentary_scope_invalid")
    from .notice_c_authenticated_web_retention import FIELD, validated_private_web_field
    _, payload = _shared_payload(db, row, source_ref=notice_cover_document_ref(key).source_key)
    fields = json.loads(payload["body"], object_pairs_hook=_unique_object)
    if (type(fields) is not dict or set(fields) != {
            "cover", "source_field", "source_record", "private_bibliography",
        } or fields["cover"] is not None or type(fields["private_bibliography"]) is not dict):
        raise ValueError("notice_private_documentary_body_invalid")
    # Keep the entire private link reference, not just its connection
    # generation, in the exact owner's envelope. Sharing descriptive
    # bytes must never provide another account's URL or link evidence.
    fields[FIELD] = _shared_pointer(row)["private_web"]
    if validated_private_web_field(fields[FIELD], key.provider_id) is None:
        raise ValueError("notice_private_documentary_web_invalid")
    # The normal admission replay below MUST reproduce the original
    # per-source revision including its owner, credential and access epoch.
    return json.dumps(fields, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _release_shared_payload_txn(db, raw, *, document):
    row = json.loads(raw, object_pairs_hook=_unique_object)
    if _SHARED_FIELD not in row:
        return
    key, payload = _shared_payload(db, row, source_ref=document.source_key)
    if len(payload["refs"]) == 1:
        # Ordinary exact-source retention, not deletion of C or its private
        # retained web sidecar. Last reference releases only the shared bytes.
        db.execute("DELETE FROM metadata WHERE key=?", (key,))
    else:
        payload["refs"].remove(document.source_key)
        db.execute("UPDATE metadata SET value=? WHERE key=?", (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), key,
        ))


def share_native_private_documentary_txn(db, source, *, edition):
    """Factor identical native parsed document bytes inside the existing writer.

    The caller has qualified the actual same-call parse and native edition
    relation. Each independently observed source keeps its own observation
    revision, source epoch, owner, connection epoch and retention sidecar.
    This is documentary equivalence only: never remote-account equivalence.
    """
    if not db.in_transaction or type(edition) is not str or _SHARED_EDITION.fullmatch(edition) is None:
        raise ValueError("notice_private_documentary_relation_invalid")
    obs, doc = source.observation, source.document
    raw, current, fence = _checked_source_state(db, doc, _opaque_key(doc))
    if current != source or fence is None:
        raise ValueError("notice_private_documentary_stale")
    row = json.loads(raw[0], object_pairs_hook=_unique_object)
    if _SHARED_FIELD in row:
        if _shared_pointer(row)["edition"] != edition:
            raise ValueError("notice_private_documentary_relation_changed")
        return
    from .notice_c_authenticated_web_retention import FIELD, validated_private_web_field
    fields = obs.fields_copy()
    if (obs.key.visibility != "private" or obs.key.branch_id != "search.authenticated.catalogue"
            or obs.parser_id != "core.search.authenticated-web" or obs.parser_version != "1"
            or obs.status != "complete" or row["field_path"] != ["cover"]
            or row["cover_inventory_complete"] is not False
            or set(fields) != {"cover", "source_field", "source_record", "private_bibliography", FIELD}
            or type(fields["private_bibliography"]) is not dict
            or validated_private_web_field(fields[FIELD], doc.provider_id) is None):
        return
    private_web = fields.pop(FIELD)
    body = json.dumps(fields, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    key = _documentary_key(doc.provider_id, edition, body)
    row.pop("raw_json")
    row[_SHARED_FIELD] = {"edition": edition, "digest": key[len(_SHARED_PREFIX):],
                          "private_web": private_web}
    existing = db.execute("SELECT 1 FROM metadata WHERE key=?", (key,)).fetchone()
    if existing is None:
        payload = {"body": body, "refs": [doc.source_key]}
    else:
        _, payload = _shared_payload(db, row, source_ref=doc.source_key, adding=True)
        if payload["body"] != body or len(payload["refs"]) >= _MAX_SHARED_REFS:
            raise ValueError("notice_private_documentary_conflict")
        payload["refs"] = sorted([*payload["refs"], doc.source_key])
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    compact = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_BYTES or len(compact.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("notice_private_documentary_too_large")
    db.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) "
               "DO UPDATE SET value=excluded.value", (key, encoded))
    if _decode(compact, doc, db=db) != source:
        raise ValueError("notice_private_documentary_replay_changed")
    db.execute("UPDATE metadata SET value=? WHERE key=?", (compact, _opaque_key(doc)))


def _decode(raw: object, document: CoverDocumentScope, *, db=None) -> PersistedNoticeCover:
    """Fail closed on tampered DB bytes; never interpret them as 'no cover'."""
    if (type(raw) is not str or len(raw.encode("utf-8")) > _MAX_BYTES
            or not raw):
        raise ValueError("notice_c_persisted_corrupt")
    try:
        row = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(row) is not dict
                or set(row) not in (_ROW_FIELDS, _ROW_FIELDS | {_LAST_SEEN},
                                   _SHARED_ROW_FIELDS, _SHARED_ROW_FIELDS | {_LAST_SEEN})
                or type(row["schema"]) is not int or row["schema"] != 1):
            raise ValueError
        key_values = row["key"]
        if type(key_values) is not dict or set(key_values) != _SOURCE_FIELDS:
            raise ValueError
        key = NoticeSourceKey(**key_values)
        if (type(row["epoch"]) is not int
                or not 1 <= row["epoch"] <= _MAX_EPOCH
                or type(row["source_revision"]) is not str
                or ("raw_json" in row and type(row["raw_json"]) is not str)
                or type(row["field_path"]) is not list
                or type(row["cover_inventory_complete"]) is not bool
                or type(row.get(_LAST_SEEN, row["observed_at"])) is not int
                or not row["observed_at"] <= row.get(_LAST_SEEN, row["observed_at"]) <= _MAX_EPOCH):
            raise ValueError
        observation = NoticeSourceObservation(
            key=key, parser_id=row["parser_id"],
            parser_version=row["parser_version"],
            observed_at=row["observed_at"], status=row["status"],
            source_revision=row["source_revision"], raw_json=(
                _shared_raw_json(db, row, key) if _SHARED_FIELD in row else row["raw_json"]),
        )
        attribute = project_notice_cover_attribute(
            observation, field_path=tuple(row["field_path"]),
            cover_inventory_complete=row["cover_inventory_complete"],
        )
        if attribute.document != document:
            raise ValueError
    except (TypeError, ValueError, KeyError, AttributeError, UnicodeError,
            OverflowError, RecursionError) as exc:
        raise ValueError("notice_c_persisted_corrupt") from exc
    return PersistedNoticeCover(observation, attribute, row["epoch"])


def _advance_seen_watermark(db: Any, key: str, encoded: str, seen_at: int) -> None:
    """Persist freshness without changing source evidence or N2 epoch.

    The enclosing BEGIN IMMEDIATE + storage lock serializes concurrent
    writers. A newer partial or identical source must prevent an older
    *changed* complete observation from winning on worker-completion order.
    """
    row = json.loads(encoded)
    previous = row.get(_LAST_SEEN, row["observed_at"])
    if seen_at <= previous:
        return
    row[_LAST_SEEN] = seen_at
    updated = json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )
    if len(updated.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("notice_c_persisted_too_large")
    db.execute("UPDATE metadata SET value=? WHERE key=?", (updated, key))


def read_notice_cover(storage: Any, document: CoverDocumentScope) -> PersistedNoticeCover | None:
    """Read one exact persisted source in O(1), even after process restart.

    A cache MISS is unknown, not a negative documentary observation. The
    caller can safely supply the resulting provenance to an N2 projection;
    this read never selects a different source on title/ISBN/image equality.
    """
    key = _opaque_key(document)
    if key is None:
        return None
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return _decode(row[0], document, db=db) if row is not None else None



def _fence_key(key: str) -> str:
    return _FENCE_PREFIX + key[len(_PREFIX):]


def _source_fingerprint(observation, field_path, inventory_complete):
    # source_revision already binds exact scope, parser/version and raw
    # fields. The projection contract is separately part of the proof.
    raw = json.dumps(
        [observation.source_revision, list(field_path), inventory_complete],
        ensure_ascii=True, separators=(",", ":"),
    )
    return sha256(raw.encode("ascii")).hexdigest()


def _is_public_search_v2(observation):
    return (observation.parser_id == "core.search.public-cover"
            and observation.parser_version == "2")


def _retained_public_search_v2(db, observation, document):
    # Old fences have only a digest, not the parser version. The exact
    # independently admitted self C is sufficient positive evidence.
    # Do not infer it from the URL, title or a foreign canonical group.
    from .notice_c_cover_index import canonical_cover_document_ref, _db_key, _decode
    canonical = canonical_cover_document_ref(observation.key.edition_id)
    raw = db.execute("SELECT value FROM metadata WHERE key=?",
                     (_db_key(canonical),)).fetchone()
    if raw is None:
        return False
    return any(member.source_ref == document.source_key
               and member.visibility == "public"
               and member.public_bibliography is not None
               for member in _decode(raw[0], canonical).sources)


def _read_source_fence(db, key):
    row = db.execute("SELECT value FROM metadata WHERE key=?",
                     (_fence_key(key),)).fetchone()
    return _decode_source_fence(None if row is None else row[0])


def _decode_source_fence(raw):
    """Decode a fence selected by its exact key, including joined reads."""
    if raw is None:
        return None
    try:
        if type(raw) is not str or len(raw) > 256:
            raise ValueError
        fence = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(fence) is not dict
                or (set(fence) - {"public_search_v2"}) not in (
                    {"epoch", "last_seen_at", "proof"},
                    {"epoch", "last_seen_at", "proof", "evicted"},
                )
                or ("public_search_v2" in fence and fence["public_search_v2"] is not True)
                or ("evicted" in fence and (
                    fence["evicted"] is not True or fence.get("epoch", 0) == 1
                ))
                or type(fence["epoch"]) is not int
                or not 1 <= fence["epoch"] <= _MAX_EPOCH
                or type(fence["last_seen_at"]) is not int
                or not 1 <= fence["last_seen_at"] <= _MAX_EPOCH
                or type(fence["proof"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", fence["proof"]) is None):
            raise ValueError
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ValueError("notice_c_source_fence_corrupt") from exc
    return fence


def _write_source_fence(db, key, *, epoch, seen_at, proof, evicted=False,
                        public_search_v2=False):
    if not 1 <= epoch <= _MAX_EPOCH or not 1 <= seen_at <= _MAX_EPOCH:
        raise ValueError("notice_c_source_fence_overflow")
    db.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_fence_key(key), json.dumps({
            "epoch": epoch, "last_seen_at": seen_at, "proof": proof,
            **({"evicted": True} if evicted else {}),
            **({"public_search_v2": True} if public_search_v2 else {}),
        }, sort_keys=True, separators=(",", ":"))),
    )



def _check_live_source_fence(previous, old_row, fence):
    """One fence invariant for an already decoded live source and its wire row."""
    if fence is None:
        return  # Historical rows without a fence remain readable.
    seen_at = old_row.get(_LAST_SEEN, previous.observation.observed_at)
    proof = _source_fingerprint(
        previous.observation, tuple(old_row["field_path"]),
        old_row["cover_inventory_complete"],
    )
    if (fence.get("evicted") is True or fence["epoch"] != previous.epoch
            or fence["last_seen_at"] != seen_at or fence["proof"] != proof
            or (fence.get("public_search_v2") is True
                and not _is_public_search_v2(previous.observation))):
        raise ValueError("notice_c_source_fence_corrupt")


def _checked_source_state(db, document, key):
    row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    previous = _decode(row[0], document, db=db) if row is not None else None
    fence = _read_source_fence(db, key)
    if previous is not None:
        _check_live_source_fence(previous, json.loads(row[0]), fence)
    return row, previous, fence


def read_current_notice_cover(storage: Any, document: CoverDocumentScope) -> PersistedNoticeCover | None:
    """Admit exact-source N2 evidence under one read snapshot.

    Unlike the raw retained-row reader, this rejects a contradictory
    durable fence. A historical source without a fence remains readable.
    No writes, migration, supplier I/O or canonical fallback.
    """
    key = _opaque_key(document)
    if key is None:
        return None
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        _, previous, _ = _checked_source_state(db, document, key)
        return previous


def _qualified_write_epoch(previous, fence):
    if previous is not None:
        return previous.epoch
    if fence is None:
        return 0
    if fence.get("evicted") is not True:
        # A legacy DELETE may have left the last live epoch. Do not expose
        # that epoch as an absence CAS token: an old worker owns it too.
        raise ValueError("notice_c_unqualified_eviction")
    return fence["epoch"]


def read_notice_cover_write_epoch(storage: Any, document: CoverDocumentScope) -> int:
    """Core-only CAS token, not a documentary revision or image snapshot.

    Zero means no observation or fence. A qualified eviction has its own
    generation. A raw deletion is detected but cannot expose a CAS token
    until a fresh, independently admitted non-CAS observation is written.
    """
    key = _opaque_key(document)
    if key is None:
        raise ValueError("notice_c_persisted_scope_invalid")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        _, previous, fence = _checked_source_state(db, document, key)
        return _qualified_write_epoch(previous, fence)


def evict_notice_cover(
    storage: Any, document: CoverDocumentScope, *, expected_epoch: int,
) -> bool:
    """Atomic exact retention with the SAME durable per-source fence.

    No automatic TTL, no provider dispatch, no deletion of C/N3 or EPUB.
    Only the authorized Core retention producer may invoke this port.
    """
    key = _opaque_key(document)
    if (key is None or type(expected_epoch) is not int
            or not 0 <= expected_epoch < _MAX_EPOCH):
        raise ValueError("notice_c_persisted_epoch_invalid")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row, previous, fence = _checked_source_state(db, document, key)
        epoch = _qualified_write_epoch(previous, fence)
        if expected_epoch != epoch:
            raise ValueError("notice_c_persisted_epoch_changed")
        if previous is None:
            return False
        if epoch >= _MAX_EPOCH - 1:
            raise ValueError("notice_c_persisted_epoch_overflow")
        old_row = json.loads(row[0])
        _write_source_fence(
            db, key, epoch=epoch + 1,
            seen_at=old_row.get(_LAST_SEEN, previous.observation.observed_at),
            proof=_source_fingerprint(
                previous.observation, tuple(old_row["field_path"]),
                old_row["cover_inventory_complete"],
            ),
            evicted=True, public_search_v2=_is_public_search_v2(previous.observation),
        )
        _release_shared_payload_txn(db, row[0], document=document)
        db.execute("DELETE FROM metadata WHERE key=?", (key,))
        return True


def _encode_source_row(observation, field_path, cover_inventory_complete, epoch):
    """Shared bounded wire encoder; this function alone writes nothing."""
    payload = {
        "schema": 1, "key": asdict(observation.key),
        "parser_id": observation.parser_id,
        "parser_version": observation.parser_version,
        "observed_at": observation.observed_at,
        "status": observation.status,
        "source_revision": observation.source_revision,
        "raw_json": observation.raw_json,
        "field_path": list(field_path),
        "cover_inventory_complete": cover_inventory_complete,
        "epoch": epoch,
        _LAST_SEEN: observation.observed_at,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("notice_c_persisted_too_large")
    return encoded


def _persist_notice_cover_txn(
    db: Any, observation: NoticeSourceObservation,
    attribute: NoticeCoverAttribute, key: str,
    field_path: tuple[str, ...],
    cover_inventory_complete: bool,
    expected_epoch: int | None,
) -> PersistedNoticeCover | None:
    """One exact source update inside the caller's locked SQLite transaction.

    Shared by the one-source CAS writer and the bounded batched Core search
    writer. The stale/partial/watermark logic MUST be identical in both paths.
    """
    existing, previous, fence = _checked_source_state(
        db, attribute.document, key,
    )
    previous_epoch = previous.epoch if previous is not None else 0
    proof = _source_fingerprint(observation, field_path, cover_inventory_complete)
    if previous is None and fence is not None:
        # Explicit eviction advances the CAS token. A raw SQL deletion
        # leaves the old live token which MUST NOT authorize a new write.
        qualified = fence.get("evicted") is True
        previous_epoch = fence["epoch"]
        if expected_epoch is not None and (
            not qualified or expected_epoch != previous_epoch
        ):
            raise ValueError("notice_c_persisted_epoch_changed")
        if (observation.observed_at < fence["last_seen_at"]
                or (observation.observed_at == fence["last_seen_at"]
                    and expected_epoch is None
                    and (qualified or proof != fence["proof"]))):
            raise ValueError("notice_c_persisted_stale_source")
        # Legacy cache-only parser v1 must not erase an admitted v2
        # bibliography when the source body is absent. No freshness update:
        # as with a live downgrade, this is not a new public observation.
        if (observation.parser_id == "core.search.public-cover"
                and observation.parser_version == "1"
                and (fence.get("public_search_v2") is True
                     or _retained_public_search_v2(db, observation, attribute.document))):
            return None
        # A newer changed incomplete observation cannot reconstruct the
        # evicted proof. In particular it would shadow the retained complete
        # C in exact-source lookup and search merely because a parser omitted
        # an image. Keep the row absent (unknown) and advance ONLY freshness.
        # The digest also recognizes an exact replay of an originally pending
        # proof without adding a second source body or a schema field.
        # A qualified authenticated reference observation is complete for
        # its own field, even though its independent image inventory is
        # pending. Allow a newer exact link proof to rehydrate that source
        # after qualified eviction; never make a negative cover assertion.
        complete_private_web = False
        if (qualified and observation.key.visibility == "private"
                and observation.key.branch_id == "search.authenticated.catalogue"
                and observation.parser_id == "core.search.authenticated-web"
                and observation.parser_version == "1"
                and observation.status == "complete"
                and field_path == ("cover",) and not cover_inventory_complete):
            from .notice_c_authenticated_web_retention import (
                FIELD, validated_private_web_field,
            )
            complete_private_web = validated_private_web_field(
                observation.fields_copy().get(FIELD), observation.key.provider_id,
            ) is not None
        if not attribute.snapshot_complete and proof != fence["proof"] and not complete_private_web:
            if observation.observed_at > fence["last_seen_at"]:
                _write_source_fence(
                    db, key, epoch=previous_epoch,
                    seen_at=observation.observed_at, proof=fence["proof"],
                    evicted=qualified,
                    public_search_v2=fence.get("public_search_v2") is True,
                )
            return None
    elif expected_epoch is not None and expected_epoch != previous_epoch:
        raise ValueError("notice_c_persisted_epoch_changed")
    if previous is not None:
        old = previous.observation
        old_row = json.loads(existing[0])
        seen_at = old_row.get(_LAST_SEEN, old.observed_at)
        old_proof = _source_fingerprint(
            old, tuple(old_row["field_path"]), old_row["cover_inventory_complete"],
        )

        def retain(*, advance):
            # Migrate a legacy row lazily; keep the same epoch and body.
            # This fence update commits atomically with the row watermark.
            latest = max(seen_at, observation.observed_at) if advance else seen_at
            if advance and latest > seen_at:
                _advance_seen_watermark(db, key, existing[0], latest)
            if fence is None or latest > seen_at:
                _write_source_fence(db, key, epoch=previous_epoch,
                                    seen_at=latest, proof=old_proof,
                                    public_search_v2=_is_public_search_v2(old))
            return previous
        # A cache replay or generic cover-only SearchResult is never a new
        # PUBLIC bibliography observation. After a same-call C v2 writer has
        # admitted its source fields, a later legacy v1 cover-only write may
        # not erase them, even with a newer local completion timestamp.
        # This is a monotone *Core parser evidence* rule, not a supplier
        # preference, visibility heuristic or N3 cache decision.
        if (old.parser_id == observation.parser_id == "core.search.public-cover"
                and old.parser_version == "2"
                and observation.parser_version == "1"):
            return retain(advance=False)
        # A delayed worker must not replace a newer accepted observation,
        # even if the latter was partial and kept an older valid cover.
        changed = (old.source_revision != observation.source_revision
                   or previous.attribute != attribute)
        if observation.observed_at < seen_at and changed:
            raise ValueError("notice_c_persisted_stale_source")
        if (previous.attribute.snapshot_complete
                and not attribute.snapshot_complete):
            # Incomplete parsing/cover inventory is no tombstone. Persist
            # its *time* (not its untrusted media) to reject older work.
            return retain(advance=True)
        if not changed:
            return retain(advance=True)
        # Same-second source changes require the explicit writer CAS.
        # The watermark is durable even if the current source is older.
        if observation.observed_at == seen_at and expected_epoch is None:
            raise ValueError("notice_c_persisted_stale_source")
    if previous_epoch >= _MAX_EPOCH:
        raise ValueError("notice_c_persisted_epoch_overflow")
    epoch = previous_epoch + 1
    encoded = _encode_source_row(observation, field_path, cover_inventory_complete, epoch)
    if existing is not None:
        _release_shared_payload_txn(db, existing[0], document=attribute.document)
    db.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, encoded),
    )
    _write_source_fence(db, key, epoch=epoch,
                        seen_at=observation.observed_at, proof=proof,
                        public_search_v2=_is_public_search_v2(observation))
    return PersistedNoticeCover(observation, attribute, epoch)


def persist_notice_cover(
    storage: Any, observation: NoticeSourceObservation, *,
    field_path: tuple[str, ...],
    cover_inventory_complete: bool,
    expected_epoch: int | None = None,
) -> PersistedNoticeCover | None:
    """Persist one *already parsed and admitted* exact Notice C source.

    The caller is an authorized Core producer, not GTK or a cache HIT.
    Optional epoch CAS protects concurrent source workers. Even without CAS,
    an older timestamp or incomplete later observation cannot silently
    replace an already complete source while it is retained. Same proof
    is idempotent on a live row. The compact fence survives source eviction:
    a fresh admitted no-CAS publication starts a strictly newer generation;
    an old CAS or changed same-second proof cannot recreate the row.
    A changed partial proof after eviction returns None: the watermark
    advances, no observation is recreated and the retained C stays readable.
    A complete admitted observation can subsequently rehydrate the source.
    A public Search v1 cache replay cannot supersede known v2 evidence.
    """
    attribute = project_notice_cover_attribute(
        observation, field_path=field_path,
        cover_inventory_complete=cover_inventory_complete,
    )
    key = _opaque_key(attribute.document)
    if key is None:
        raise ValueError("notice_c_persisted_scope_invalid")
    if (expected_epoch is not None
            and (type(expected_epoch) is not int
                 or not 0 <= expected_epoch < _MAX_EPOCH)):
        raise ValueError("notice_c_persisted_epoch_invalid")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return _persist_notice_cover_txn(
            db, observation, attribute, key, field_path,
            cover_inventory_complete, expected_epoch,
        )


# Bound writer-lock hold time for 30,000-row catalogues while replacing one
# SQLite transaction per source with a bounded set of shared transactions.
_BATCH_LIMIT = 128


class _ChunkSourcesRefused(ValueError):
    def __init__(self, retry_positions):
        super().__init__("notice_c_chunk_sources_refused")
        self.retry_positions = retry_positions


def persist_notice_cover_many(
    storage: Any, observations: list[NoticeSourceObservation], *,
    field_path: tuple[str, ...],
    cover_inventory_complete: bool,
    _after_persist=None, _before_chunk_commit=None, _before_persist=None,
) -> list[PersistedNoticeCover | None]:
    """Persist an already parsed Core batch, preserving each source's CAS.

    Return one result (or None) per input. A malformed, stale or conflicting
    row must not suppress another *independent* admitted source. Each entry
    has a SAVEPOINT so the old single-source rejection semantics survive,
    while only one BEGIN/commit and SQLite connection is used per 128 rows.
    The private pre-write callback may refuse a row without rolling back
    its diagnostic metadata. It runs inside the same row savepoint.
    The private chunk callback receives only accepted row snapshots, before
    commit. A closed list of refused accepted row positions permits one
    rollback and local replay of only the other accepted rows; generic
    failures roll back the chunk without salvage. Return receipts only
    after commit, and continue independent later chunks.
    Source keys and bytes are never logged. An absent image is still UNKNOWN.
    This never grants rights, downloads images, or copies N3 media.
    """
    if type(observations) is not list:
        raise TypeError("expected Core Notice C batch")
    prepared: list[
        tuple[NoticeSourceObservation, NoticeCoverAttribute, str] | None
    ] = []
    for observation in observations:
        try:
            attribute = project_notice_cover_attribute(
                observation, field_path=field_path,
                cover_inventory_complete=cover_inventory_complete,
            )
            key = _opaque_key(attribute.document)
            if key is None:
                raise ValueError("notice_c_persisted_scope_invalid")
            prepared.append((observation, attribute, key))
        except (ValueError, TypeError, UnicodeError, OverflowError,
                RecursionError):
            prepared.append(None)

    def write_chunk(positions):
        accepted = {}
        gated = []
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for position in positions:
                observation, attribute, key = prepared[position]
                db.execute("SAVEPOINT notice_c_batch_row")
                try:
                    if _before_persist is not None and _before_persist(
                            db, observation, attribute, position) is not True:
                        gated.append(position)
                        continue
                    published = _persist_notice_cover_txn(
                        db, observation, attribute, key, field_path,
                        cover_inventory_complete, None,
                    )
                    if published is not None and _after_persist is not None:
                        _after_persist(db, published, position)
                    if published is not None:
                        accepted[position] = published
                except (ValueError, TypeError, UnicodeError, OverflowError,
                        RecursionError, sqlite3.Error):
                    db.execute("ROLLBACK TO SAVEPOINT notice_c_batch_row")
                finally:
                    db.execute("RELEASE SAVEPOINT notice_c_batch_row")
            if _before_chunk_commit is not None:
                rejected = _before_chunk_commit(db, tuple(accepted.items()))
                if rejected is not None:
                    if (type(rejected) is not tuple
                            or any(type(p) is not int or p not in accepted for p in rejected)
                            or len(set(rejected)) != len(rejected)):
                        raise ValueError("notice_c_chunk_invalid_refusal")
                    if rejected:
                        raise _ChunkSourcesRefused(
                            tuple(sorted(gated + [p for p in accepted if p not in rejected])),
                        )
        return accepted

    results: list[PersistedNoticeCover | None] = [None] * len(prepared)
    for offset in range(0, len(prepared), _BATCH_LIMIT):
        positions = tuple(p for p in range(offset, min(offset + _BATCH_LIMIT, len(prepared)))
                          if prepared[p] is not None)
        if not positions:
            continue
        # At most two local write attempts, both with a complete new SQLite
        # snapshot; never split an admitted group or publish a partial intent.
        for attempt in range(2):
            try:
                accepted = write_chunk(positions)
            except _ChunkSourcesRefused as refused:
                if attempt or not refused.retry_positions:
                    break
                positions = refused.retry_positions
                continue
            except (ValueError, TypeError, UnicodeError, OverflowError,
                    RecursionError, OSError, sqlite3.Error):
                if _before_chunk_commit is None:
                    raise
                break
            for position, published in accepted.items():
                results[position] = published
            break
    return results


__all__ = [
    "PersistedNoticeCover", "read_notice_cover", "persist_notice_cover",
    "persist_notice_cover_many",
    "is_notice_cover_document", "read_notice_cover_write_epoch",
    "evict_notice_cover",
]
