"""#4982: inert receipt for real public raw input retained by its producer.

The existing TTL cache holds only opaque reference/digests, NOT response
bytes, URLs, headers, parser callbacks or a copy of Notice C. The trusted
Core producer must have matched the actual authorized response to the exact
source and to an independently retained, replayable input. This receipt is
an equality fence, not proof of transport admission or parser availability.
No current provider is enabled by importing this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from uuid import uuid4

from .notice_cover_repository import (
    PersistedNoticeCover, _checked_source_state, _opaque_key,
)
from .book_cover_projection import CoverDocumentScope
from .notice_parse_witness import admitted_source_scope_sha256

_NAMESPACE = "notice.c.raw-input-receipt.v1"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_MAX_BYTES = 2 * 1024 * 1024
_MAX_TTL = 86400
_MAX_CLOCK = 2**63 - 1
_MAX_RECEIPTS = 2048


@dataclass(frozen=True, slots=True, repr=False)
class PublicRawInputReceipt:
    source_scope_digest: str
    source_state_digest: str
    transport_digest: str
    parser_family_digest: str
    parser_implementation_digest: str
    input_digest: str
    byte_count: int
    generation_token: str
    producer_token: str | None = None


def _hash(value):
    return sha256(value).hexdigest()


def _digest(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _clock(now):
    if type(now) is not int or not 1 <= now <= _MAX_CLOCK:
        raise ValueError("notice_raw_input_invalid_clock")


def _key(document):
    if (type(document) is not CoverDocumentScope
            or document.kind != "public_catalogue"
            or document.identity_id is not None
            or document.credential_id != "__public__"
            or document.account_id is not None):
        raise ValueError("notice_raw_input_public_scope_required")
    key = _opaque_key(document)
    if key is None:
        raise ValueError("notice_raw_input_invalid_scope")
    return key


def _coverless_public_search_observation(observation):
    """Only the explicit Search v2 thumbnail omission, not generic partial data."""
    key = observation.key
    if (key.visibility != "public" or key.branch_id != "search.public.catalogue"
            or observation.parser_id != "core.search.public-cover"
            or observation.parser_version != "2" or observation.status != "partial"):
        return False
    result = observation.fields_copy().get("search_result")
    return (type(result) is dict
            and set(result) in ({"thumbnail_url", "public_bibliography"},
                               {"thumbnail_url", "public_bibliography", "public_web_references"})
            and result["thumbnail_url"] is None
            and type(result["public_bibliography"]) is dict)


def _source_state(db, document):
    key = _key(document)
    raw, source, fence = _checked_source_state(db, document, key)
    if source is None or source.observation.key.visibility != "public":
        return None, None
    if source.observation.status != "complete":
        if (not _coverless_public_search_observation(source.observation)
                or source.attribute.field_path != ("search_result", "thumbnail_url")
                or source.attribute.state != "pending" or source.attribute.candidates
                or json.loads(raw[0]).get("cover_inventory_complete") is not True):
            return None, None
        # Reuse the durable bibliographic decoder and exact coordinates.
        # This exception is for a real C source, never a generic partial parser.
        from .notice_c_public_search import _public_observation
        hit = _public_observation(raw[0], key_string=key,
                                  now=source.observation.observed_at)
        if hit is None or hit.document != document:
            return None, None
    # Include the watermark and eviction fence, not only the visible epoch.
    state = json.dumps([_hash(raw[0].encode("utf-8")), fence],
                       ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return source, _hash(state.encode("ascii"))


def _valid(receipt):
    return (type(receipt) is PublicRawInputReceipt
            and all(_digest(getattr(receipt, name)) for name in (
                "source_scope_digest", "source_state_digest", "transport_digest",
                "parser_family_digest", "parser_implementation_digest", "input_digest",
            ))
            and type(receipt.byte_count) is int
            and 1 <= receipt.byte_count <= _MAX_BYTES
            and type(receipt.generation_token) is str
            and _TOKEN.fullmatch(receipt.generation_token) is not None
            and (receipt.producer_token is None or (
                type(receipt.producer_token) is str
                and _TOKEN.fullmatch(receipt.producer_token) is not None)))


def _read(db, key, now):
    row = db.execute(
        "SELECT payload,stored_at,expires_at FROM cache "
        "WHERE namespace=? AND cache_key=?",
        (_NAMESPACE, key),
    ).fetchone()
    if row is None:
        return None
    raw, stored, expires = row
    if (type(stored) is not int or type(expires) is not int
            or not 1 <= stored <= now < expires <= _MAX_CLOCK
            or not 1 <= expires - stored <= _MAX_TTL):
        return None
    try:
        if type(raw) is not str or len(raw) > 1024:
            raise ValueError
        receipt = PublicRawInputReceipt(**json.loads(raw))
        if not _valid(receipt) or _encode(receipt) != raw:
            raise ValueError
        return receipt
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ValueError("notice_raw_input_corrupt_receipt") from exc


def _encode(receipt):
    return json.dumps(asdict(receipt), ensure_ascii=True,
                      sort_keys=True, separators=(",", ":"))


def _body_digest(body):
    if type(body) is not bytes or not 1 <= len(body) <= _MAX_BYTES:
        raise ValueError("notice_raw_input_invalid_bytes")
    return _hash(body)


def record_public_raw_input_receipt(
    storage, source, body, *, transport_digest, parser_family_digest,
    parser_implementation_digest, now, ttl_seconds, producer_token=None,
):
    """Record only an opaque receipt after an actual retained raw response.

    Core-only qualification port, not a plugin claim. Caller owns the
    response provenance, parser identity and retention of the original
    bytes; it must not pass parsed DTOs or reconstruct bytes from C.
    An optional producer_token is the opaque cache-only lookup coordinate,
    never a URL or authority to fetch. A missing token cannot schedule replay.
    TTL must not exceed the producer's actual raw-input retention window.
    """
    _clock(now)
    if (type(source) is not PersistedNoticeCover
            or type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= _MAX_TTL
            or now > _MAX_CLOCK - ttl_seconds
            or (producer_token is not None and (
                type(producer_token) is not str or _TOKEN.fullmatch(producer_token) is None))
            or not all(_digest(value) for value in (
                transport_digest, parser_family_digest, parser_implementation_digest,
            ))):
        raise ValueError("notice_raw_input_invalid_receipt")
    key = _key(source.document)
    input_digest = _body_digest(body)
    # In particular the exact saved projection is not a response body.
    if body == source.observation.raw_json.encode("utf-8"):
        raise ValueError("notice_raw_input_projected_only")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current, state = _source_state(db, source.document)
        if current is None or current != source or source.observation.observed_at > now:
            raise ValueError("notice_raw_input_source_changed")
        scope = admitted_source_scope_sha256(
            source.observation.key, parser_id=source.observation.parser_id,
        )
        old = _read(db, key, now)
        fields = (scope, state, transport_digest, parser_family_digest,
                  parser_implementation_digest, input_digest, len(body))
        if old is not None and old == PublicRawInputReceipt(*fields, old.generation_token, producer_token):
            # Replay never extends retention; the owner may shorten it.
            db.execute(
                "UPDATE cache SET expires_at=MIN(expires_at,?) "
                "WHERE namespace=? AND cache_key=?",
                (now + ttl_seconds, _NAMESPACE, key),
            )
            return old
        count = db.execute(
            "SELECT count(*) FROM (SELECT cache_key FROM cache "
            "WHERE namespace=? AND expires_at>? AND cache_key<>? LIMIT ?)",
            (_NAMESPACE, now, key, _MAX_RECEIPTS),
        ).fetchone()[0]
        if count >= _MAX_RECEIPTS:
            raise ValueError("notice_raw_input_capacity")
        receipt = PublicRawInputReceipt(*fields, uuid4().hex, producer_token)
        db.execute(
            "INSERT INTO cache(namespace,cache_key,payload,stored_at,expires_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(namespace,cache_key) DO UPDATE SET "
            "payload=excluded.payload,stored_at=excluded.stored_at,expires_at=excluded.expires_at",
            (_NAMESPACE, key, _encode(receipt), now, now + ttl_seconds),
        )
        return receipt


def check_public_raw_input_txn(db, document, receipt, body, *, now):
    """Exact current source + receipt + bytes. No IO beyond this SQLite read.

    Must be repeated at the eventual source publication transaction; a
    successful check is NOT a proof that a newly invoked parser is trusted.
    The producer must independently verify source authority and parser ABI.
    """
    if not db.in_transaction:
        raise ValueError("notice_raw_input_transaction_required")
    _clock(now)
    if not _valid(receipt):
        raise ValueError("notice_raw_input_invalid_receipt")
    current = _read(db, _key(document), now)
    if current != receipt:
        raise ValueError("notice_raw_input_receipt_changed")
    source, state = _source_state(db, document)
    if (source is None or state != receipt.source_state_digest
            or admitted_source_scope_sha256(
                source.observation.key, parser_id=source.observation.parser_id,
            ) != receipt.source_scope_digest):
        raise ValueError("notice_raw_input_source_changed")
    if (_body_digest(body) != receipt.input_digest
            or len(body) != receipt.byte_count):
        raise ValueError("notice_raw_input_bytes_changed")
    return source


def read_public_raw_input_receipt(storage, document, *, now):
    """Return a current receipt or None, not an input availability claim."""
    _clock(now)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        receipt = _read(db, _key(document), now)
        if receipt is None:
            return None
        source, state = _source_state(db, document)
        if (source is None or state != receipt.source_state_digest
                or admitted_source_scope_sha256(
                    source.observation.key, parser_id=source.observation.parser_id,
                ) != receipt.source_scope_digest):
            return None
        return receipt
