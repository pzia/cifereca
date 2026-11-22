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
from unicodedata import category

from .derived_variant_region_paths import PRIVATE_DERIVED_REGION

_PREFIX = "derived.publication.pending.v2:"
_END = _PREFIX[:-1] + ";"
_COMMITTED = "derived.publication.committed.v2:"
_COMMITTED_END = _COMMITTED[:-1] + ";"
_LEGACY_SCHEMA = "derived.intent.v2"
_PRIVATE_SCHEMA = "derived.intent.v3"
_MAX_RECORDS = 1024
_MAX_RECORD_BYTES = 8192
_MAX_TOTAL_BYTES = 8 * 1024 * 1024
# Conservative cumulative output-size budget, not measured disk usage.
# Both pending and committed receipts count; no implicit reclamation.
_MAX_RESERVED_OUTPUT_BYTES = 16 * 1024 ** 3


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


@dataclass(frozen=True, slots=True, repr=False)
class DerivedReservation:
    key: str
    receipt_sha256: str


def _receipt_digest(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text(value, *, required=True, limit=1024):
    return (type(value) is str and len(value) <= limit
            and (bool(value) or not required)
            and all(category(ch) not in ("Cc", "Cf", "Cs", "Zl", "Zp")
                    for ch in value))


def _digest(value):
    return (type(value) is str and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value))


def _stamp(value, length):
    return (type(value) is tuple and len(value) == length
            and all(type(x) is int and 0 <= x <= 2**63 - 1 for x in value)
            and value[2] > 0)


def _validate(intent, *, schema=_LEGACY_SCHEMA):
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
            or len(set(paths)) != 3 or candidate.parent != parent.parent):
        raise DerivedJournalConflict("derived_journal_invalid_intent")
    if schema == _LEGACY_SCHEMA:
        valid_path = stage.parent == parent.parent / ".cifereca-staging" / "variants"
    elif schema == _PRIVATE_SCHEMA:
        profile = stage.parent.parent
        valid_path = (
            stage.parent.name == PRIVATE_DERIVED_REGION
            and profile != Path("/") and parent.is_relative_to(profile)
            and parent != profile and not stage.name.startswith(".")
            and not any(p.startswith(".") for p in parent.relative_to(profile).parts)
            and not candidate.name.startswith(".")
        )
    else:
        valid_path = False
    if not valid_path:
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


def _recipe_slot(identity_id, placement_id, transformer_id, profile_id, options_fingerprint):
    fields = (identity_id, placement_id, transformer_id, profile_id, options_fingerprint)
    return _PREFIX + hashlib.sha256(_encode(fields).encode("ascii")).hexdigest()


def _slot(intent):
    return _recipe_slot(intent.identity_id, intent.parent_placement_id,
                        intent.transformer_id, intent.profile_id, intent.options_fingerprint)


def require_no_pending_derived_recipe(
    storage, *, identity_id, placement_id, transformer_id,
    profile_id, options_fingerprint,
):
    """Read-only early refusal, not an admission or a replacement for reserve.

    An interrupted exact recipe must receive a supervised review before a
    repeat transform allocates new staging bytes. A concurrent reservation
    can still occur after this observation; only the existing transaction
    in reserve_derived_publication authorizes the publication slot.
    """
    if (not all(_text(value) for value in (identity_id, placement_id, transformer_id))
            or not all(_text(value, required=False)
                       for value in (profile_id, options_fingerprint))):
        raise DerivedJournalConflict("derived_journal_invalid_intent")
    key = _recipe_slot(identity_id, placement_id, transformer_id, profile_id,
                       options_fingerprint)
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise DerivedJournalConflict("derived_journal_nested_transaction")
        # Use a single read snapshot for both the exact slot and the
        # paired receipt invariant. Reserve repeats these checks atomically;
        # this early veto avoids allocating expensive staging bytes.
        with db:
            db.execute("BEGIN")
            if db.execute("SELECT 1 FROM metadata WHERE key=?", (key,)).fetchone():
                raise DerivedJournalConflict("derived_journal_slot_occupied")
            from .derived_variant_compact_receipt import _measure_compact_in_transaction
            _measure_compact_in_transaction(db, require_paired=True)


def _decode(key, raw):
    try:
        # SQLite's dynamic typing can return a BLOB even for TEXT columns.
        # json.loads accepts bytes, but a receipt digest is defined on text.
        # Such a row must fail with a closed journal code, not an AttributeError.
        if (type(key) is not str or type(raw) is not str
                or not 0 < len(raw.encode("utf-8")) <= _MAX_RECORD_BYTES):
            raise ValueError()
        record = json.loads(raw, object_pairs_hook=_unique_pairs)
        if type(record) is not dict:
            raise ValueError()
        schema = record.get("schema")
        expected = {"schema", "nonce", "intent"}
        if schema == _PRIVATE_SCHEMA:
            expected.add("region_binding_sha256")
            if not _digest(record.get("region_binding_sha256")) or _encode(record) != raw:
                raise ValueError()
        if (set(record) != expected or schema not in (_LEGACY_SCHEMA, _PRIVATE_SCHEMA)
                or type(record["nonce"]) is not str or len(record["nonce"]) != 32
                or any(ch not in "0123456789abcdef" for ch in record["nonce"])
                or type(record["intent"]) is not dict):
            raise ValueError()
        data = record["intent"]
        data["parent_stamp"] = tuple(data["parent_stamp"])
        data["output_stamp"] = tuple(data["output_stamp"])
        intent = DerivedIntent(**data)
        _validate(intent, schema=schema)
        if key not in (_slot(intent), _COMMITTED + _receipt_digest(raw)):
            raise ValueError()
        return intent
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError,
            UnicodeError):
        raise DerivedJournalConflict("derived_journal_corrupt") from None


def _read_logical_receipt(db, key):
    """Read the exact original bytes, even for an authorized compact-only row.

    Physical corrupt values are returned to the existing decoder (or
    partial capacity report). Missing or ambiguous authority fails closed.
    """
    if type(key) is not str:
        raise DerivedJournalConflict("derived_journal_corrupt")
    row = db.execute("SELECT CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND ? "
                     "THEN value END FROM metadata WHERE key=?", (_MAX_RECORD_BYTES, key)).fetchone()
    if key.startswith(_COMMITTED) and _digest(key[len(_COMMITTED):]):
        from .derived_variant_compact_receipt import _retired_receipt
        retired = _retired_receipt(db, key)
        if retired is not None:
            return retired
    if row is None:
        raise DerivedJournalConflict("derived_journal_receipt_missing")
    return row[0]


def _measure(db):
    # Read at most one overflow row. No unbounded JSON materialization.
    rows = db.execute(
        "SELECT key,length(CAST(value AS BLOB)) FROM metadata "
        "WHERE (key>=? AND key<?) OR (key>=? AND key<?) "
        "ORDER BY key LIMIT ?",
        (_PREFIX, _END, _COMMITTED, _COMMITTED_END, _MAX_RECORDS + 1),
    ).fetchall()
    if len(rows) > _MAX_RECORDS:
        raise DerivedJournalConflict("derived_journal_capacity_exceeded")
    # Logical charging remains identical after compacting the physical
    # representation. No historical digest, target or quota slot disappears.
    from .derived_variant_compact_receipt import _retired_receipts
    retired = _retired_receipts(db)
    rows = sorted((*rows, *((key, len(raw.encode("ascii"))) for key, raw in retired.items())))
    if len(rows) > _MAX_RECORDS:
        raise DerivedJournalConflict("derived_journal_capacity_exceeded")
    if any(type(size) is not int or not 0 < size <= _MAX_RECORD_BYTES
           for _, size in rows):
        raise DerivedJournalConflict("derived_journal_corrupt")
    total = sum(size for _, size in rows)
    if total > _MAX_TOTAL_BYTES:
        raise DerivedJournalConflict("derived_journal_capacity_exceeded")
    return rows, total


def _reserve_in_transaction(db, intent, raw, *, private=False):
    """Shared quota and slot transaction; private admission verifies FS first."""
    key = _slot(intent)
    if (not db.in_transaction or type(private) is not bool
            or _decode(key, raw) != intent
            or (json.loads(raw, object_pairs_hook=_unique_pairs)["schema"] == _PRIVATE_SCHEMA) != private):
        raise DerivedJournalConflict("derived_journal_invalid_intent")
    size = len(raw.encode("ascii"))
    # A missing original with a surviving shadow is not an
    # approved quota refund. Validate the separate namespace
    # in the same serialized admission transaction.
    from .derived_variant_compact_receipt import _measure_compact_in_transaction
    _measure_compact_in_transaction(db, require_paired=True)
    rows, total = _measure(db)
    if len(rows) >= _MAX_RECORDS or total + size > _MAX_TOTAL_BYTES:
        raise DerivedJournalConflict("derived_journal_capacity_exceeded")
    reserved_output_bytes = 0
    occupied = False
    for existing_key, _ in rows:
        existing = _read_logical_receipt(db, existing_key)
        prior = _decode(existing_key, existing)
        reserved_output_bytes += prior.output_size
        # Committed receipt targets remain reserved until a
        # supervised retirement, even if the Placement is lost.
        if (prior.candidate_path == intent.candidate_path
                or (existing_key.startswith(_PREFIX) and (
                    existing_key == key or prior.staged_path == intent.staged_path))
                or (private and (prior.staged_path == intent.staged_path
                                 or prior.output_stamp[:2] == intent.output_stamp[:2]))):
            occupied = True
    # Validate all bounded receipts before reporting a collision.
    # Corrupt later rows must still yield a corruption veto.
    if occupied:
        raise DerivedJournalConflict("derived_journal_slot_occupied")
    if reserved_output_bytes + intent.output_size > _MAX_RESERVED_OUTPUT_BYTES:
        raise DerivedJournalConflict("derived_journal_output_capacity_exceeded")
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
    return DerivedReservation(key, _receipt_digest(raw))


def reserve_derived_publication(storage, intent: DerivedIntent) -> DerivedReservation:
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
                _reserve_in_transaction(db, intent, raw)
        finally:
            db.execute(f"PRAGMA synchronous={int(synchronous)}")
    return DerivedReservation(key, _receipt_digest(raw))


def reserve_derived_intent(storage, intent: DerivedIntent) -> str:
    """Compatibility for the original private reservation API."""
    return reserve_derived_publication(storage, intent).key


def _read_pending_receipt(db, reservation):
    if (not db.in_transaction or type(reservation) is not DerivedReservation
            or type(reservation.key) is not str
            or not reservation.key.startswith(_PREFIX)
            or not _digest(reservation.key[len(_PREFIX):])
            or not _digest(reservation.receipt_sha256)):
        raise DerivedJournalConflict("derived_journal_reservation_invalid")
    row = db.execute(
        "SELECT value FROM metadata WHERE key=? "
        "AND length(CAST(value AS BLOB)) BETWEEN 1 AND ?",
        (reservation.key, _MAX_RECORD_BYTES),
    ).fetchone()
    # TEXT affinity does not prevent a BLOB from being persisted in SQLite.
    # The digest is defined only for Unicode text: fail closed rather than
    # leaking AttributeError from bytes.encode at the recovery boundary.
    if (row is None or type(row[0]) is not str
            or _receipt_digest(row[0]) != reservation.receipt_sha256):
        raise DerivedJournalConflict("derived_journal_reservation_changed")
    intent = _decode(reservation.key, row[0])
    record = json.loads(row[0], object_pairs_hook=_unique_pairs)
    return row[0], intent, record


def _read_pending_reservation(db, reservation, *, _private_guard=None):
    raw, intent, record = _read_pending_receipt(db, reservation)
    if record["schema"] != _LEGACY_SCHEMA:
        # Only the isolated v3 recovery controller can supply the typed
        # exact guard. A generic callback, receipt digest or legacy caller
        # cannot turn admission into publication authority.
        from .derived_variant_private_recovery import _PrivateRecoveryGuard
        if (type(_private_guard) is not _PrivateRecoveryGuard
                or _private_guard.verify(db, reservation, intent, record) is not True):
            raise DerivedJournalConflict("derived_journal_private_region_review_required")
    elif _private_guard is not None:
        raise DerivedJournalConflict("derived_journal_private_region_review_required")
    return raw, intent


def require_derived_reservation_in_transaction(db, reservation, *, expected,
                                               _private_guard=None):
    """Compare the private reservation with exact publication coordinates."""
    _raw, intent = _read_pending_reservation(
        db, reservation, **({"_private_guard": _private_guard} if _private_guard is not None else {}))
    if any(getattr(intent, key) != value for key, value in expected.items()):
        raise DerivedJournalConflict("derived_journal_reservation_mismatch")
    return intent


def _require_canonical_proof(db, intent, *, placement_id, origin_id, recipe_id):
    """Verify the exact canonical SQL witness, without changing state."""
    artifact_id = "sha256:" + intent.output_sha256
    placement = db.execute(
        "SELECT artifact_id,identity_id,path,state,provider_id FROM placements "
        "WHERE placement_id=?", (placement_id,),
    ).fetchone()
    artifact = db.execute(
        "SELECT sha256,size FROM artifacts WHERE artifact_id=?", (artifact_id,),
    ).fetchone()
    origin = db.execute(
        "SELECT origin_kind,identity_id,artifact_id,source_reference_id,"
        "parent_artifact_id,evidence_ref,metadata_json FROM origin_links "
        "WHERE origin_link_id=?", (origin_id,),
    ).fetchone()
    recipe = db.execute(
        "SELECT artifact_id,source_artifact_id,source_sha256,transformer_plugin_id,"
        "options_fingerprint FROM artifact_variants WHERE variant_id=?", (recipe_id,),
    ).fetchone()
    parent_id = "sha256:" + intent.parent_sha256
    if (placement != (artifact_id, intent.identity_id, intent.candidate_path,
                      "present", "cifereca")
            or artifact != (intent.output_sha256, intent.output_size)
            or origin is None or origin[:6] != (
                "derived", intent.identity_id, artifact_id, None, parent_id,
                "derived:" + recipe_id + ":" + placement_id)
            or recipe != (artifact_id, parent_id, intent.parent_sha256,
                          intent.transformer_id, intent.options_fingerprint)):
        raise DerivedJournalConflict("derived_journal_canonical_proof_missing")
    # Canonical provenance is a bounded JSON TEXT witness, never an arbitrary
    # SQLite BLOB (which json.loads would otherwise silently accept).
    if (type(origin[6]) is not str
            or not 0 < len(origin[6].encode("utf-8")) <= _MAX_RECORD_BYTES):
        raise DerivedJournalConflict("derived_journal_canonical_proof_missing")
    try:
        metadata = json.loads(origin[6], object_pairs_hook=_unique_pairs)
    except (ValueError, TypeError, RecursionError):
        raise DerivedJournalConflict("derived_journal_canonical_proof_missing") from None
    if (type(metadata) is not dict
            or any(metadata.get(key) != value for key, value in {
                "source_placement_id": intent.parent_placement_id,
                "transformer_plugin_id": intent.transformer_id,
                "transformer_version": intent.transformer_version,
                "profile_plugin_id": intent.profile_id,
                "profile_version": intent.profile_version,
                "options_fingerprint": intent.options_fingerprint,
                "source_sha256": intent.parent_sha256,
                "result_sha256": intent.output_sha256,
            }.items())):
        raise DerivedJournalConflict("derived_journal_canonical_proof_missing")


def commit_derived_reservation_in_transaction(
    db, reservation, *, intent, placement_id, origin_id, recipe_id, _private_guard=None,
):
    """Move the exact receipt to history atomically with canonical B.

    Only the Core writer calls this inside its existing BEGIN IMMEDIATE.
    The receipt payload is unchanged, counts against the same quota, and
    never authorizes a new publication by itself.
    """
    raw, current = _read_pending_reservation(
        db, reservation, **({"_private_guard": _private_guard} if _private_guard is not None else {}))
    if current != intent:
        raise DerivedJournalConflict("derived_journal_reservation_changed")
    _require_canonical_proof(db, intent, placement_id=placement_id,
                             origin_id=origin_id, recipe_id=recipe_id)
    committed_key = _COMMITTED + reservation.receipt_sha256
    from .derived_variant_compact_receipt import _RETIRED_PREFIX
    if db.execute("SELECT 1 FROM metadata WHERE key IN (?,?)",
                  (committed_key, _RETIRED_PREFIX + reservation.receipt_sha256)).fetchone():
        raise DerivedJournalConflict("derived_journal_reservation_changed")
    updated = db.execute(
        "UPDATE metadata SET key=? WHERE key=? AND value=?",
        (committed_key, reservation.key, raw),
    )
    if updated.rowcount != 1:
        raise DerivedJournalConflict("derived_journal_reservation_changed")


def summarize_derived_journal(storage):
    """Aggregate only, no payload, path, digest or recovery authority."""
    with storage._lock:
        db = storage._connect()
        # A borrowed connection may already own a caller transaction. Never
        # enter its context manager (which could commit/rollback that work).
        if db.in_transaction:
            raise DerivedJournalConflict("derived_journal_nested_transaction")
        with db:
            db.execute("BEGIN")
            rows, total = _measure(db)
            pending_bytes = committed_bytes = 0
            for key, _ in rows:
                raw = _read_logical_receipt(db, key)
                intent = _decode(key, raw)
                if key.startswith(_PREFIX):
                    pending_bytes += intent.output_size
                else:
                    committed_bytes += intent.output_size
    return {
        "count": len(rows), "payload_bytes": total,
        "pending_count": sum(key.startswith(_PREFIX) for key, _ in rows),
        "committed_count": sum(key.startswith(_COMMITTED) for key, _ in rows),
        "record_capacity": _MAX_RECORDS, "payload_capacity": _MAX_TOTAL_BYTES,
        "pending_output_bytes": pending_bytes,
        "committed_output_bytes": committed_bytes,
        "reserved_output_bytes": pending_bytes + committed_bytes,
        "reserved_output_capacity": _MAX_RESERVED_OUTPUT_BYTES,
        "physical_disk_usage_measured": False,
        "recovery_authorized": False, "destructive_cleanup_allowed": False,
    }
