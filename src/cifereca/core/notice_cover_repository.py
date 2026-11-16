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
    if (document.kind not in ("catalogue", "public_catalogue")
            or _EXACT_SOURCE.fullmatch(document.source_key) is None):
        return None
    return _PREFIX + document.source_key[len("notice-c:v1:"):]


def is_notice_cover_document(document: CoverDocumentScope) -> bool:
    """Pure eligibility for exact-source or explicit canonical C read-through."""
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


def _decode(raw: object, document: CoverDocumentScope) -> PersistedNoticeCover:
    """Fail closed on tampered DB bytes; never interpret them as 'no cover'."""
    if (type(raw) is not str or len(raw.encode("utf-8")) > _MAX_BYTES
            or not raw):
        raise ValueError("notice_c_persisted_corrupt")
    try:
        row = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(row) is not dict
                or set(row) not in (_ROW_FIELDS, _ROW_FIELDS | {_LAST_SEEN})
                or type(row["schema"]) is not int or row["schema"] != 1):
            raise ValueError
        key_values = row["key"]
        if type(key_values) is not dict or set(key_values) != _SOURCE_FIELDS:
            raise ValueError
        key = NoticeSourceKey(**key_values)
        if (type(row["epoch"]) is not int
                or not 1 <= row["epoch"] <= _MAX_EPOCH
                or type(row["source_revision"]) is not str
                or type(row["raw_json"]) is not str
                or type(row["field_path"]) is not list
                or type(row["cover_inventory_complete"]) is not bool
                or type(row.get(_LAST_SEEN, row["observed_at"])) is not int
                or not row["observed_at"] <= row.get(_LAST_SEEN, row["observed_at"]) <= _MAX_EPOCH):
            raise ValueError
        observation = NoticeSourceObservation(
            key=key, parser_id=row["parser_id"],
            parser_version=row["parser_version"],
            observed_at=row["observed_at"], status=row["status"],
            source_revision=row["source_revision"], raw_json=row["raw_json"],
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
        row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return _decode(row[0], document) if row is not None else None


def _persist_notice_cover_txn(
    db: Any, observation: NoticeSourceObservation,
    attribute: NoticeCoverAttribute, key: str,
    field_path: tuple[str, ...],
    cover_inventory_complete: bool,
    expected_epoch: int | None,
) -> PersistedNoticeCover:
    """One exact source update inside the caller's locked SQLite transaction.

    Shared by the one-source CAS writer and the bounded batched Core search
    writer. The stale/partial/watermark logic MUST be identical in both paths.
    """
    existing = db.execute(
        "SELECT value FROM metadata WHERE key=?", (key,),
    ).fetchone()
    previous = _decode(existing[0], attribute.document) if existing else None
    previous_epoch = previous.epoch if previous is not None else 0
    if expected_epoch is not None and expected_epoch != previous_epoch:
        raise ValueError("notice_c_persisted_epoch_changed")
    if previous is not None:
        old = previous.observation
        # A cache replay or generic cover-only SearchResult is never a new
        # PUBLIC bibliography observation. After a same-call C v2 writer has
        # admitted its source fields, a later legacy v1 cover-only write may
        # not erase them, even with a newer local completion timestamp.
        # This is a monotone *Core parser evidence* rule, not a supplier
        # preference, visibility heuristic or N3 cache decision.
        if (old.parser_id == observation.parser_id == "core.search.public-cover"
                and old.parser_version == "2"
                and observation.parser_version == "1"):
            return previous
        seen_at = json.loads(existing[0]).get(_LAST_SEEN, old.observed_at)
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
            _advance_seen_watermark(
                db, key, existing[0], observation.observed_at,
            )
            return previous
        if not changed:
            _advance_seen_watermark(
                db, key, existing[0], observation.observed_at,
            )
            return previous
        # Same-second source changes require the explicit writer CAS.
        # The watermark is durable even if the current source is older.
        if observation.observed_at == seen_at and expected_epoch is None:
            raise ValueError("notice_c_persisted_stale_source")
        if previous_epoch >= _MAX_EPOCH:
            raise ValueError("notice_c_persisted_epoch_overflow")
    epoch = previous_epoch + 1
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
    db.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, encoded),
    )
    return PersistedNoticeCover(observation, attribute, epoch)


def persist_notice_cover(
    storage: Any, observation: NoticeSourceObservation, *,
    field_path: tuple[str, ...],
    cover_inventory_complete: bool,
    expected_epoch: int | None = None,
) -> PersistedNoticeCover:
    """Persist one *already parsed and admitted* exact Notice C source.

    The caller is an authorized Core producer, not GTK or a cache HIT.
    Optional epoch CAS protects concurrent source workers. Even without CAS,
    an older timestamp or incomplete later observation cannot silently
    replace an already complete source. Same proof is idempotent.
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


def persist_notice_cover_many(
    storage: Any, observations: list[NoticeSourceObservation], *,
    field_path: tuple[str, ...],
    cover_inventory_complete: bool,
) -> list[PersistedNoticeCover | None]:
    """Persist an already parsed Core batch, preserving each source's CAS.

    Return one result (or None) per input. A malformed, stale or conflicting
    row must not suppress another *independent* admitted source. Each entry
    has a SAVEPOINT so the old single-source rejection semantics survive,
    while only one BEGIN/commit and SQLite connection is used per 128 rows.
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

    results: list[PersistedNoticeCover | None] = [None] * len(prepared)
    for offset in range(0, len(prepared), _BATCH_LIMIT):
        chunk = prepared[offset:offset + _BATCH_LIMIT]
        if not any(item is not None for item in chunk):
            continue
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for position, item in enumerate(chunk, offset):
                if item is None:
                    continue
                observation, attribute, key = item
                db.execute("SAVEPOINT notice_c_batch_row")
                try:
                    results[position] = _persist_notice_cover_txn(
                        db, observation, attribute, key, field_path,
                        cover_inventory_complete, None,
                    )
                except (ValueError, TypeError, UnicodeError, OverflowError,
                        RecursionError, sqlite3.Error):
                    db.execute("ROLLBACK TO SAVEPOINT notice_c_batch_row")
                finally:
                    db.execute("RELEASE SAVEPOINT notice_c_batch_row")
    return results


__all__ = [
    "PersistedNoticeCover", "read_notice_cover", "persist_notice_cover",
    "persist_notice_cover_many",
    "is_notice_cover_document",
]
