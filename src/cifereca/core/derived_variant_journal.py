"""Private SQLite reservation journal for derived publications (#4520).

Foundation only: a reservation proves neither a completed hardlink nor an
OriginLink. Do not wire a writer until its commit/recovery protocol is ready.
No filesystem receipt, automatic replay, deletion or plugin API.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from secrets import token_hex

_PREFIX = "derived.publication.pending.v2:"
_END = _PREFIX[:-1] + ";"
_MAX_RECORDS = 1024
_MAX_RECORD_BYTES = 8192
_MAX_TOTAL_BYTES = 8 * 1024 * 1024


class DerivedJournalConflict(ValueError):
    """Closed, non-sensitive refusal code."""


@dataclass(frozen=True, slots=True, repr=False)
class DerivedIntent:
    identity_id: str
    parent_placement_id: str
    parent_sha256: str
    parent_path: str
    staged_path: str
    candidate_path: str
    output_sha256: str
    output_size: int
    parent_stamp: tuple[int, int, int, int, int]
    output_stamp: tuple[int, int, int, int]
    transformer_id: str
    transformer_version: str
    profile_id: str
    profile_version: str
    options_fingerprint: str
    variant_kind: str
    media_format: str


def _text(value, *, required=True, limit=1024):
    return (type(value) is str and len(value) <= limit
            and (bool(value) or not required)
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))


def _digest(value):
    return (type(value) is str and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value))


def _stamp(value, length):
    return (type(value) is tuple and len(value) == length
            and all(type(x) is int and 0 <= x <= 2**63 - 1 for x in value)
            and value[2] > 0)


def _validate(intent):
    if type(intent) is not DerivedIntent:
        raise DerivedJournalConflict("derived_journal_invalid_intent")
    if (not all(_text(field) for field in (
            intent.identity_id, intent.parent_placement_id, intent.transformer_id,
            intent.variant_kind, intent.media_format))
            or not all(_text(field, required=False) for field in (
                intent.transformer_version, intent.profile_id,
                intent.profile_version, intent.options_fingerprint))
            or not _digest(intent.parent_sha256) or not _digest(intent.output_sha256)
            or intent.parent_sha256 == intent.output_sha256
            or not _stamp(intent.parent_stamp, 5)
            or not _stamp(intent.output_stamp, 4)
            or type(intent.output_size) is not int
            or intent.output_size != intent.output_stamp[2]
            or intent.media_format not in ("epub", "kepub", "pdf")):
        raise DerivedJournalConflict("derived_journal_invalid_intent")
    paths = (intent.parent_path, intent.staged_path, intent.candidate_path)
    if not all(_text(path, limit=4096) for path in paths):
        raise DerivedJournalConflict("derived_journal_invalid_intent")
    parent, stage, candidate = map(Path, paths)
    if (not all(path.is_absolute() and path.name and ".." not in path.parts
                and str(path) == raw for path, raw in zip((parent, stage, candidate), paths))
            or len(set(paths)) != 3 or candidate.parent != parent.parent
            or stage.parent != parent.parent / ".cifereca-staging" / "variants"):
        raise DerivedJournalConflict("derived_journal_invalid_intent")


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DerivedJournalConflict("derived_journal_corrupt")
        result[key] = value
    return result


def _slot(intent):
    fields = (intent.identity_id, intent.parent_placement_id, intent.transformer_id,
              intent.profile_id, intent.options_fingerprint)
    return _PREFIX + hashlib.sha256(_encode(fields).encode("ascii")).hexdigest()


def _decode(key, raw):
    try:
        record = json.loads(raw, object_pairs_hook=_unique_pairs)
        if (type(record) is not dict or set(record) != {"schema", "nonce", "intent"}
                or record["schema"] != "derived.intent.v2"
                or type(record["nonce"]) is not str or len(record["nonce"]) != 32
                or any(ch not in "0123456789abcdef" for ch in record["nonce"])
                or type(record["intent"]) is not dict):
            raise ValueError()
        data = record["intent"]
        data["parent_stamp"] = tuple(data["parent_stamp"])
        data["output_stamp"] = tuple(data["output_stamp"])
        intent = DerivedIntent(**data)
        _validate(intent)
        if _slot(intent) != key:
            raise ValueError()
        return intent
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise DerivedJournalConflict("derived_journal_corrupt") from None


def _measure(db):
    # Read at most one overflow row. No unbounded JSON materialization.
    rows = db.execute(
        "SELECT key,length(CAST(value AS BLOB)) FROM metadata "
        "WHERE key>=? AND key<? ORDER BY key LIMIT ?",
        (_PREFIX, _END, _MAX_RECORDS + 1),
    ).fetchall()
    if len(rows) > _MAX_RECORDS:
        raise DerivedJournalConflict("derived_journal_capacity_exceeded")
    if any(type(size) is not int or not 0 < size <= _MAX_RECORD_BYTES
           for _, size in rows):
        raise DerivedJournalConflict("derived_journal_corrupt")
    total = sum(size for _, size in rows)
    if total > _MAX_TOTAL_BYTES:
        raise DerivedJournalConflict("derived_journal_capacity_exceeded")
    return rows, total


def reserve_derived_intent(storage, intent: DerivedIntent) -> str:
    """Reserve a recipe/target slot in one durable SQL transaction.

    Call only after Core has verified the exact physical input/output and
    before any publication link. This function does not verify those bytes,
    make an approval, or grant permission to replay. No caller is wired yet.
    SQLite serializes competing processes sharing this DB. FULL applies to
    this transaction only; existing connection settings are restored.
    """
    _validate(intent)
    key = _slot(intent)
    raw = _encode({"schema": "derived.intent.v2", "nonce": token_hex(16),
                   "intent": asdict(intent)})
    size = len(raw.encode("ascii"))
    if size > _MAX_RECORD_BYTES:
        raise DerivedJournalConflict("derived_journal_invalid_intent")
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise DerivedJournalConflict("derived_journal_nested_transaction")
        synchronous = db.execute("PRAGMA synchronous").fetchone()[0]
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                db.execute("BEGIN IMMEDIATE")
                rows, total = _measure(db)
                if len(rows) >= _MAX_RECORDS or total + size > _MAX_TOTAL_BYTES:
                    raise DerivedJournalConflict("derived_journal_capacity_exceeded")
                for existing_key, _ in rows:
                    existing = db.execute(
                        "SELECT value FROM metadata WHERE key=?", (existing_key,),
                    ).fetchone()[0]
                    prior = _decode(existing_key, existing)
                    if (existing_key == key or prior.candidate_path == intent.candidate_path
                            or prior.staged_path == intent.staged_path):
                        raise DerivedJournalConflict("derived_journal_slot_occupied")
                parent = db.execute(
                    "SELECT artifact_id,identity_id,path,state FROM placements "
                    "WHERE placement_id=?", (intent.parent_placement_id,),
                ).fetchone()
                artifact = db.execute(
                    "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
                    ("sha256:" + intent.parent_sha256,),
                ).fetchone()
                if (parent != ("sha256:" + intent.parent_sha256, intent.identity_id,
                               intent.parent_path, "present")
                        or artifact != (intent.parent_sha256, intent.parent_stamp[2])):
                    raise DerivedJournalConflict("derived_journal_parent_changed")
                if db.execute("SELECT 1 FROM placements WHERE path=? LIMIT 1",
                              (intent.candidate_path,)).fetchone():
                    raise DerivedJournalConflict("derived_journal_slot_occupied")
                db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (key, raw))
        finally:
            db.execute(f"PRAGMA synchronous={int(synchronous)}")
    return key


def summarize_derived_journal(storage):
    """Aggregate only, no payload, path, digest or recovery authority."""
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        rows, total = _measure(db)
        for key, _ in rows:
            raw = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0]
            _decode(key, raw)
    return {
        "count": len(rows), "payload_bytes": total,
        "record_capacity": _MAX_RECORDS, "payload_capacity": _MAX_TOTAL_BYTES,
        "recovery_authorized": False, "destructive_cleanup_allowed": False,
    }
