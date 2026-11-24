"""#4552: transactional SQLite port for descriptive materializations.

A trusted Core producer supplies a transaction-local source snapshot reader.
No plugin execution or network access is allowed inside this transaction.
The journal lease and completion remain a separate required integration gate.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json

from .notice_descriptive_materialization import (
    DescriptiveMaterialization, require_current_descriptive_materialization,
)

_PREFIX = "notice.c.descriptive.v1:"
_MAX_ROW_BYTES = 8192


def _row_key(row: DescriptiveMaterialization) -> str:
    if type(row) is not DescriptiveMaterialization:
        raise ValueError("invalid_descriptive_materialization")
    coordinates = (row.target_edition_id, row.canonical_type, row.scope)
    if any(type(v) is not str or not 0 < len(v) <= 160 for v in coordinates):
        raise ValueError("invalid_descriptive_materialization")
    raw = json.dumps(coordinates, ensure_ascii=True, separators=(",", ":"))
    return _PREFIX + sha256(raw.encode("ascii")).hexdigest()


def _decode(raw):
    if type(raw) is not str:
        raise ValueError("corrupt_descriptive_materialization")
    try:
        raw_size = len(raw.encode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("corrupt_descriptive_materialization") from exc
    if not 0 < raw_size <= _MAX_ROW_BYTES:
        raise ValueError("corrupt_descriptive_materialization")
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("corrupt_descriptive_materialization")
            out[key] = value
        return out
    try:
        payload = json.loads(raw, object_pairs_hook=unique)
        if type(payload) is not dict or set(payload) != set(DescriptiveMaterialization.__dataclass_fields__):
            raise ValueError
        refs = payload["source_evidence_keys"]
        if type(refs) is not list or any(type(ref) is not list for ref in refs):
            raise ValueError
        if (len(refs) > 128 or any(
            len(ref) != 3 or any(type(value) is not str for value in ref)
            for ref in refs
        )):
            raise ValueError
        if (type(payload["version"]) is not int
                or type(payload["inherited"]) is not bool
                or any(type(payload[field]) is not str for field in (
                    "execution_key", "canonical_type", "target_edition_id",
                    "scope", "plugin_id", "plugin_version", "schema_id", "value",
                ))):
            raise ValueError
        # JSON escapes can decode to unpaired surrogates even when raw bytes
        # were valid UTF-8. Reject them before reconstructing a trusted row.
        scalar_fields = (
            "execution_key", "canonical_type", "target_edition_id",
            "scope", "plugin_id", "plugin_version", "schema_id", "value",
        )
        for value in (
            *(payload[field] for field in scalar_fields),
            *(part for ref in refs for part in ref),
        ):
            value.encode("utf-8")
        payload["source_evidence_keys"] = tuple(tuple(ref) for ref in refs)
        return DescriptiveMaterialization(**payload)
    except (TypeError, ValueError, KeyError, UnicodeError, RecursionError) as exc:
        raise ValueError("corrupt_descriptive_materialization") from exc


def publish_descriptive_materialization(
    storage, row: DescriptiveMaterialization, *,
    expected_execution_key: str | None, capture_current_txn,
    journal_lease=None, journal_now=None, journal_clock=None,
):
    """CAS publish with authoritative Core snapshot recaptured in same txn.

    capture_current_txn(db) MUST be a trusted, side-effect-free Core reader
    returning (selection, admitted, installed_source_sha256). This is not a
    plugin callback and must not perform I/O outside this SQLite connection.
    With journal_lease supplied, validates the exact journal generation,
    fence, target and execution digests and completes it atomically. Without
    the lease this is a staging-only port, not a scheduler publication.
    """
    key = _row_key(row)
    if journal_clock is not None and (journal_lease is None or not callable(journal_clock)):
        raise ValueError("descriptive_journal_binding_required")
    if (journal_lease is None) != (journal_now is None):
        raise ValueError("descriptive_journal_binding_required")
    if expected_execution_key is not None:
        if (type(expected_execution_key) is not str
                or not expected_execution_key.startswith("notice-c-exec-v1:")
                or len(expected_execution_key) != len("notice-c-exec-v1:") + 64
                or any(ch not in "0123456789abcdef"
                       for ch in expected_execution_key[len("notice-c-exec-v1:"):])):
            raise ValueError("invalid_descriptive_cas")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        existing = _decode(previous[0]) if previous else None
        if (existing.execution_key if existing else None) != expected_execution_key:
            raise ValueError("descriptive_materialization_cas_changed")
        selection, admitted, digest = capture_current_txn(db)
        require_current_descriptive_materialization(
            row, selection=selection, admitted=admitted,
            installed_source_sha256=digest,
        )
        if journal_lease is not None:
            from . import notice_c_job_journal as journal
            from .notice_c_cover_index import canonical_cover_document_ref
            job = journal._held(db, journal_lease,
                                journal_clock() if journal_clock is not None else journal_now)
            if (job.spec.kind != "recompute_aggregate"
                    or job.spec.target != canonical_cover_document_ref(
                        row.target_edition_id).source_key
                    or job.spec.input_digest != sha256(
                        row.execution_key.encode("ascii")
                    ).hexdigest()
                    or job.spec.implementation_digest != digest):
                raise ValueError("descriptive_journal_snapshot_mismatch")
        encoded = json.dumps(asdict(row), ensure_ascii=True, sort_keys=True,
                             separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_ROW_BYTES:
            raise ValueError("descriptive_materialization_unbounded")
        db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, encoded),
        )
        if journal_lease is not None:
            journal._complete_job_txn(
                db, journal_lease,
                now=journal_clock() if journal_clock is not None else journal_now,
            )
    return row


def read_descriptive_materialization(storage, *, target_edition_id, canonical_type,
                                     scope, capture_current_txn):
    """Fail closed on stale or tampered persisted entries, never serve as right."""
    coordinate = DescriptiveMaterialization(
        1, "", canonical_type, target_edition_id, scope, "", "", "", "", False, (),
    )
    key = _row_key(coordinate)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        found = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if found is None:
            return None
        row = _decode(found[0])
        if _row_key(row) != key:
            raise ValueError("corrupt_descriptive_materialization")
        selection, admitted, digest = capture_current_txn(db)
        return require_current_descriptive_materialization(
            row, selection=selection, admitted=admitted,
            installed_source_sha256=digest,
        )
