"""#6937: lossless v1 logical receipt codec and separate capacity preflight.

No live writer, quota refund, physical exclusivity or replay authorization.
An authorized compact-only representation reconstructs the exact old receipt;
its historical coordinates are not thereby proven currently valid.
"""
from dataclasses import fields
import json

from .derived_variant_journal import (
    DerivedIntent, DerivedJournalConflict, _COMMITTED, _decode, _digest,
    _encode, _receipt_digest, _unique_pairs,
    _MAX_RECORDS, _MAX_RECORD_BYTES, _MAX_TOTAL_BYTES, _LEGACY_SCHEMA, _PRIVATE_SCHEMA,
)

_PREFIX = "derived.publication.compact.v1:"
_END = _PREFIX[:-1] + ";"
_RETIRED_PREFIX = "derived.publication.compact-retired.v1:"
_RETIRED_END = _RETIRED_PREFIX[:-1] + ";"
# Exactly one 64-byte witness SHA per compact-only original, at most one
# original per existing compact record. Separate bounded logical trace.
_MAX_RETIREMENT_BYTES = _MAX_RECORDS * 64
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
_PRIVATE_COMPACT_SCHEMA = "derived.compact.v2"


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

    Canonical, bounded v2 and v3 committed bytes are representable. A legacy
    encoding with a different byte digest must remain untouched.
    """
    original_size = _raw_size(raw)
    _require(type(key) is str and key == _COMMITTED + _receipt_digest(raw))
    intent = _decode(key, raw)
    _require(tuple(field.name for field in fields(DerivedIntent)) == _FIELDS,
             "derived_compact_schema_changed")
    record = json.loads(raw, object_pairs_hook=_unique_pairs)
    _require(_encode(record) == raw)
    values = [getattr(intent, name) for name in _FIELDS]
    body = ([_PRIVATE_COMPACT_SCHEMA, record["nonce"], values, record["region_binding_sha256"]]
            if record["schema"] == _PRIVATE_SCHEMA else [_SCHEMA, record["nonce"], values])
    compact = _encode(body)
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
        _require(type(body) is list and len(body) in (3, 4)
                 and type(body[2]) is list and len(body[2]) == len(_FIELDS))
        _require(_encode(body) == raw)
        record = {"schema": _LEGACY_SCHEMA, "nonce": body[1],
                  "intent": dict(zip(_FIELDS, body[2]))}
        if len(body) == 4:
            _require(body[0] == _PRIVATE_COMPACT_SCHEMA and _digest(body[3]))
            record["schema"] = _PRIVATE_SCHEMA
            record["region_binding_sha256"] = body[3]
        else:
            _require(body[0] == _SCHEMA)
        original = _encode(record)
        _require(_raw_size(original) >= _raw_size(raw))
        committed_key = _COMMITTED + key[len(_PREFIX):]
        _require(_receipt_digest(original) == key[len(_PREFIX):])
        _decode(committed_key, original)
        return committed_key, original
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise DerivedJournalConflict("derived_compact_unverified") from None


def _retired_receipt(db, original_key):
    """Resolve one authorized compact-only row, never an orphan shadow."""
    _require(db.in_transaction and type(original_key) is str
             and original_key.startswith(_COMMITTED)
             and _digest(original_key[len(_COMMITTED):]))
    digest = original_key[len(_COMMITTED):]
    marker_key = _RETIRED_PREFIX + digest
    row = db.execute("SELECT CASE WHEN length(CAST(value AS BLOB))=64 "
                     "THEN value END FROM metadata WHERE key=?", (marker_key,)).fetchone()
    if row is None:
        return None
    _require(_digest(row[0]), "derived_compact_retirement_unverified")
    proof_key = _PREFIX + digest
    proof = db.execute("SELECT CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND ? "
                       "THEN value END FROM metadata WHERE key=?",
                       (_MAX_PROOF_BYTES, proof_key)).fetchone()
    _require(proof is not None and type(proof[0]) is str
             and _receipt_digest(proof[0]) == row[0]
             and db.execute("SELECT 1 FROM metadata WHERE key=?", (original_key,)).fetchone() is None,
             "derived_compact_retirement_unverified")
    key, original = decode_committed_witness(proof_key, proof[0])
    _require(key == original_key and _raw_size(proof[0]) + 64 < _raw_size(original),
             "derived_compact_retirement_unverified")
    return original


def _retired_receipts(db):
    """Installation-wide bounded marker validation; return logical originals."""
    _require(db.in_transaction)
    rows = db.execute("SELECT key,length(CAST(value AS BLOB)) FROM metadata "
                      "WHERE key>=? AND key<? ORDER BY key LIMIT ?",
                      (_RETIRED_PREFIX, _RETIRED_END, _MAX_PROOFS + 1)).fetchall()
    _require(len(rows) <= _MAX_PROOFS
             and 64 * len(rows) <= _MAX_RETIREMENT_BYTES,
             "derived_compact_retirement_capacity")
    result = {}
    for key, size in rows:
        _require(type(key) is str and _digest(key[len(_RETIRED_PREFIX):]) and size == 64,
                 "derived_compact_retirement_unverified")
        original_key = _COMMITTED + key[len(_RETIRED_PREFIX):]
        original = _retired_receipt(db, original_key)
        _require(original is not None, "derived_compact_retirement_unverified")
        result[original_key] = original
    return result


def _measure_compact_in_transaction(db, *, require_paired=False):
    """Bounded separate namespace, inside the caller's SQL snapshot.

    Admission requires each witness to retain its original or a validated
    atomic retirement marker. Reports may still inspect orphan shadows.
    """
    _require(db.in_transaction, "derived_compact_snapshot_required")
    retired = _retired_receipts(db)
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
        original_key, original = decode_committed_witness(key, raw)
        if require_paired:
            record = db.execute(
                "SELECT CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND ? "
                "THEN value ELSE NULL END FROM metadata WHERE key=?",
                (_MAX_RECORD_BYTES, original_key),
            ).fetchone()
            _require(record == (original,) or retired.get(original_key) == original,
                     "derived_compact_original_unverified")
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
