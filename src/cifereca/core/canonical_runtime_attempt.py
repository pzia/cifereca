"""#7231: durable negative-only fence for unqualified selected runtime replay.

Only Core creates a claim before its guarded runtime dispatch. No supplier
token, runtime recipe or exception is persisted. A changed offer or resetting
a download budget does not authorize another dispatch. Do not adopt historic
markers or use this to fence ordinary qualified content GETs.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time

from .canonical_publication import _stable
from .source_convergence import SourceKey

RUNTIME_ATTEMPT_PREFIX = "canonical.runtime_attempt.v1:"


def _key(source):
    if type(source) is not SourceKey:
        raise ValueError("canonical_runtime_attempt_invalid_source")
    s = source.scope
    return RUNTIME_ATTEMPT_PREFIX + _stable(
        "source", s.identity_id, s.provider_id, s.credential_id, s.account_id,
        source.branch, source.edition_id, source.right_id, source.kind,
    )


def _digest(offer):
    if type(offer) is not str or not 0 < len(offer) <= 1024:
        raise ValueError("canonical_runtime_attempt_invalid_offer")
    return hashlib.sha256(offer.encode()).hexdigest()


def _hex(value, size):
    return type(value) is str and len(value) == size and all(c in "0123456789abcdef" for c in value)


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _record(db, source):
    return db.execute(
        "SELECT CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND 1024 "
        "THEN value ELSE NULL END FROM metadata WHERE key=?", (_key(source),),
    ).fetchone()


def _decode(raw):
    if type(raw) is not str:
        return None
    try:
        data = json.loads(raw)
        if (type(data) is dict and _encode(data) == raw
                and type(data.get("schema")) is int
                and ((data["schema"] == 1
                      and set(data) == {"schema", "operation", "offer_digest", "started_at"})
                     or (data["schema"] == 2 and set(data) == {
                         "schema", "operation", "offer_digest", "started_at", "phase"
                     } and data["phase"] in ("runtime", "purchase_acsm")))
                and _hex(data["operation"], 32) and _hex(data["offer_digest"], 64)
                and type(data["started_at"]) is int
                and 0 < data["started_at"] <= 253402300799):
            return data
    except (ValueError, TypeError, RecursionError):
        pass
    return None


def read_runtime_attempt_in_db(db, source):
    """Return negative evidence even for a damaged marker; no private data."""
    row = _record(db, source)
    if row is None:
        return False, 0, ""
    data = _decode(row[0])
    return (True, data["started_at"], data.get("phase", "runtime")) if data is not None else (True, 0, "unknown")


def pending_runtime_attempt(storage, source):
    # A pending caller transaction must not be settled by this observation.
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("canonical_runtime_attempt_nested_transaction")
        return read_runtime_attempt_in_db(db, source)[0]


def begin_runtime_attempt(storage, *, source, offer_id, shared_lock, phase="runtime"):
    from .canonical_recovery import _exact_borrower_bound_in_db, _lock_ok
    if not _lock_ok(shared_lock):
        raise ValueError("canonical_runtime_attempt_lock_required")
    if (phase not in ("runtime", "purchase_acsm")
            or (phase == "purchase_acsm" and source.kind != "purchase")):
        raise ValueError("canonical_runtime_attempt_invalid_phase")
    operation = secrets.token_hex(16)
    raw = _encode({"schema": 2, "operation": operation, "phase": phase,
                   "offer_digest": _digest(offer_id), "started_at": int(time.time())})
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("canonical_runtime_attempt_nested_transaction")
        synchronous = db.execute("PRAGMA synchronous").fetchone()[0]
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                db.execute("BEGIN IMMEDIATE")
                if not _exact_borrower_bound_in_db(db, source.scope):
                    raise ValueError("canonical_runtime_attempt_source_rebound")
                cursor = db.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
                    (_key(source), raw),
                )
                if cursor.rowcount != 1:
                    raise ValueError("canonical_runtime_attempt_pending")
        finally:
            db.execute(f"PRAGMA synchronous={int(synchronous)}")
    return operation


def release_runtime_attempt_in_transaction(db, *, source, offer_id, operation_id):
    """CAS only the exact live claim, from a completed receipt or pre-dispatch refusal."""
    if not operation_id:
        return
    if not db.in_transaction or not _hex(operation_id, 32):
        raise ValueError("canonical_runtime_attempt_release_invalid")
    row = _record(db, source)
    data = _decode(row[0]) if row is not None else None
    if (data is None or data["operation"] != operation_id
            or data["offer_digest"] != _digest(offer_id)):
        raise ValueError("canonical_runtime_attempt_changed")
    if db.execute("DELETE FROM metadata WHERE key=? AND value=?",
                  (_key(source), row[0])).rowcount != 1:
        raise ValueError("canonical_runtime_attempt_changed")


def release_unsubmitted_runtime_attempt(storage, *, source, offer_id, operation_id, shared_lock):
    """Core alone knows this particular call has NOT entered its executor."""
    from .canonical_recovery import _lock_ok
    if not _lock_ok(shared_lock):
        raise ValueError("canonical_runtime_attempt_lock_required")
    # The standalone refusal must carry a real claim. The optional empty
    # operation is reserved for completed intake, not this explicit release.
    if not _hex(operation_id, 32):
        raise ValueError("canonical_runtime_attempt_release_invalid")
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("canonical_runtime_attempt_nested_transaction")
        synchronous = db.execute("PRAGMA synchronous").fetchone()[0]
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                db.execute("BEGIN IMMEDIATE")
                release_runtime_attempt_in_transaction(db, source=source, offer_id=offer_id,
                                                       operation_id=operation_id)
        finally:
            db.execute(f"PRAGMA synchronous={int(synchronous)}")
