"""#6937: lossless v1 logical receipt codec and separate capacity preflight.

No live writer, quota refund, retirement, physical exclusivity or replay
authorization. A compact witness is a *representation* of the old committed
receipt, not proof that its historical coordinates are currently valid.
"""
from dataclasses import fields
import json

from .derived_variant_journal import (
    DerivedIntent, DerivedJournalConflict, _COMMITTED, _decode, _digest,
    _encode, _receipt_digest, _unique_pairs,
    _MAX_RECORDS, _MAX_RECORD_BYTES, _MAX_TOTAL_BYTES,
)

_PREFIX = "derived.publication.compact.v1:"
_END = _PREFIX[:-1] + ";"
# Separate budget sized for one lossless witness per maximum-sized original
# receipt in a single generation. Every accepted encoding is <= its original
# byte size. This bounds a full shadow generation without increasing or
# refunding the active journal's existing limits.
_MAX_PROOFS = _MAX_RECORDS
_MAX_PROOF_BYTES = _MAX_RECORD_BYTES
_MAX_PROOF_TOTAL = _MAX_TOTAL_BYTES
# Freeze the positional contract, never silently inherit future field order.
_FIELDS = (
    "identity_id", "parent_placement_id", "parent_sha256", "parent_path",
    "staged_path", "candidate_path", "output_sha256", "output_size",
    "parent_stamp", "output_stamp", "transformer_id", "transformer_version",
    "profile_id", "profile_version", "options_fingerprint", "variant_kind",
    "media_format",
)
_SCHEMA = "derived.compact.v1"


def _require(condition, code="derived_compact_unverified"):
    if not condition:
        raise DerivedJournalConflict(code)


def _raw_size(raw):
    _require(type(raw) is str and len(raw) <= _MAX_PROOF_BYTES)
    try:
        size = len(raw.encode("ascii"))
    except UnicodeError:
        raise DerivedJournalConflict("derived_compact_unverified") from None
    _require(0 < size <= _MAX_PROOF_BYTES)
    return size


def encode_committed_witness(key, raw):
    """Return private (compact key, compact JSON), without changing either store.

    Only canonical, bounded v2 committed bytes are representable. A legacy
    encoding with a different byte digest must remain untouched.
    """
    original_size = _raw_size(raw)
    _require(type(key) is str and key == _COMMITTED + _receipt_digest(raw))
    intent = _decode(key, raw)
    _require(tuple(field.name for field in fields(DerivedIntent)) == _FIELDS,
             "derived_compact_schema_changed")
    record = json.loads(raw, object_pairs_hook=_unique_pairs)
    _require(_encode(record) == raw)
    compact = _encode([_SCHEMA, record["nonce"],
                       [getattr(intent, name) for name in _FIELDS]])
    _require(_raw_size(compact) <= original_size)
    proof_key = _PREFIX + _receipt_digest(raw)
    _require(decode_committed_witness(proof_key, compact) == (key, raw))
    return proof_key, compact


def decode_committed_witness(key, raw):
    """Reconstruct exact original bytes and receipt digest. No live proof."""
    _raw_size(raw)
    _require(type(key) is str and key.startswith(_PREFIX)
             and _digest(key[len(_PREFIX):]))
    try:
        body = json.loads(raw, object_pairs_hook=_unique_pairs)
        _require(type(body) is list and len(body) == 3
                 and body[0] == _SCHEMA and type(body[2]) is list
                 and len(body[2]) == len(_FIELDS))
        _require(_encode(body) == raw)
        original = _encode({"schema": "derived.intent.v2", "nonce": body[1],
                            "intent": dict(zip(_FIELDS, body[2]))})
        _require(_raw_size(original) >= _raw_size(raw))
        committed_key = _COMMITTED + key[len(_PREFIX):]
        _require(_receipt_digest(original) == key[len(_PREFIX):])
        _decode(committed_key, original)
        return committed_key, original
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise DerivedJournalConflict("derived_compact_unverified") from None


def _measure_compact_in_transaction(db):
    """Bounded separate namespace. Read only while caller holds SQL snapshot."""
    _require(db.in_transaction, "derived_compact_snapshot_required")
    rows = db.execute(
        "SELECT key,length(CAST(value AS BLOB)) FROM metadata "
        "WHERE key>=? AND key<? ORDER BY key LIMIT ?",
        (_PREFIX, _END, _MAX_PROOFS + 1),
    ).fetchall()
    _require(len(rows) <= _MAX_PROOFS, "derived_compact_capacity_exceeded")
    total = 0
    for key, size in rows:
        _require(type(size) is int and 0 < size <= _MAX_PROOF_BYTES)
        total += size
        _require(total <= _MAX_PROOF_TOTAL, "derived_compact_capacity_exceeded")
        raw = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0]
        decode_committed_witness(key, raw)
    return len(rows), total


def review_compact_capacity(storage):
    """Private aggregate preflight, not an authorization or reclaim estimate."""
    with storage._lock:
        db = storage._connect()
        _require(not db.in_transaction, "derived_compact_nested_transaction")
        with db:
            db.execute("BEGIN")
            count, total = _measure_compact_in_transaction(db)
    return {"count": count, "payload_bytes": total,
            "record_capacity": _MAX_PROOFS, "payload_capacity": _MAX_PROOF_TOTAL,
            "retention_authorized": False, "quota_refund_authorized": False,
            "destructive_cleanup_allowed": False}
