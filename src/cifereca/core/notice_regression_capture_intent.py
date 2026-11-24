"""Bounded durable capture intentions; filesystem work after commit.

Only sanitized diagnostic metadata is persisted here. Raw input must be
resolved again by an authorized caller, never copied into metadata.
"""
import hashlib
import json
import re
import time

from .notice_regression_capture import write_regression_capture

_MAX_INTENTS = 2048
_PREFIX = "notice.c.regression-capture.v1:"
_SHA = re.compile(r"[0-9a-f]{64}")


def enqueue_capture_txn(db, *, scope, generation, review, input_digest, snapshots=None):
    if (not db.in_transaction or type(scope) is not str
            or not _SHA.fullmatch(scope) or type(input_digest) is not str
            or not _SHA.fullmatch(input_digest) or type(generation) is not int
            or not 1 <= generation < 2**63
            or review.status not in ("parser_regression_confirmed", "parser_drift_suspected")
            or type(review.affected_fields) is not tuple
            or not 1 <= len(review.affected_fields) <= 32
            or any(type(f) is not str or not re.fullmatch(r"critical_field_([1-9]|[12][0-9]|3[012])", f)
                   for f in review.affected_fields)):
        raise ValueError("invalid_capture_intent")
    if snapshots is None:
        snapshots = {}
    if (type(snapshots) is not dict or any(
            name not in ("baseline.json", "last_healthy.json", "current_parse.json", "environment.json")
            or type(value) is not str for name, value in snapshots.items())
            or sum(len(v.encode("utf-8")) for v in snapshots.values()) > 16384):
        raise ValueError("invalid_capture_snapshots")
    payload = dict(generation=generation, status=review.status,
                   fields=sorted(review.affected_fields), input_sha256=input_digest)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    diagnostic = hashlib.sha256((scope + ":" + encoded).encode()).hexdigest()
    key = _PREFIX + diagnostic
    existing = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    if existing is None:
        count = db.execute("SELECT count(*) FROM metadata WHERE key GLOB ?",
                           (_PREFIX + "*",)).fetchone()[0]
        if count >= _MAX_INTENTS:
            # Reclaim only terminal metadata; never discard unfinished work.
            rows = db.execute("SELECT key,value FROM metadata WHERE key GLOB ?",
                              (_PREFIX + "*",)).fetchall()
            terminal = []
            for old_key, raw in rows:
                try:
                    old = json.loads(raw)
                    if old["capture_state"] in ("complete", "partial"):
                        terminal.append((old.get("finished_at", 0), old_key))
                except (ValueError, KeyError, TypeError):
                    continue
            if not terminal:
                return None
            db.execute("DELETE FROM metadata WHERE key=?", (min(terminal)[1],))
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)",
                   (key, json.dumps(dict(payload, snapshots=snapshots, capture_state="pending"), sort_keys=True)))
    return diagnostic


def export_capture(storage, diagnostic, *, members, directory=None):
    """Resolve one pending intent; callers must authorize all supplied members.

    No filesystem operation under the Storage lock. A failed export remains
    retryable; a successful export is immutable and idempotent.
    """
    if type(diagnostic) is not str or not _SHA.fullmatch(diagnostic):
        raise ValueError("invalid_capture_id")
    key = _PREFIX + diagnostic
    with storage._lock, storage._connect() as db:
        row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    record = json.loads(row[0])
    if record["capture_state"] in ("complete", "partial"):
        return record["capture_state"]
    try:
        if type(members) is not dict:
            raise ValueError("invalid_capture_members")
        safe = {name: value.encode("utf-8") for name, value in record.get("snapshots", {}).items()}
        if set(members).intersection(safe) or "diff.json" in members:
            raise ValueError("capture_metadata_override")
        safe.update(members)
        safe["diff.json"] = json.dumps({k: record[k] for k in
            ("status", "fields", "input_sha256")}, sort_keys=True).encode()
        if "response.raw" in safe and hashlib.sha256(safe["response.raw"]).hexdigest() != record["input_sha256"]:
            raise ValueError("capture_input_changed")
        _, state = write_regression_capture(
            diagnostic_id=diagnostic, generation=record["generation"],
            members=safe, directory=directory)
    except (OSError, ValueError):
        state = "failed"
    return _record_outcome(storage, key, row[0], record, state)


def _record_outcome(storage, key, previous, record, state):
    updated = dict(record, capture_state=state)
    if state == "failed":
        attempts = min(record.get("attempts", 0) + 1, 16)
        updated.update(attempts=attempts, retry_after=time.time() + min(3600, 2 ** attempts))
    else:
        updated.pop("retry_after", None)
        updated["finished_at"] = time.time()
        updated.pop("snapshots", None)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE metadata SET value=? WHERE key=? AND value=?",
                   (json.dumps(updated, sort_keys=True), key, previous))
    return state


def pending_capture_ids(storage, *, limit=32, now=None):
    """Bounded restart inventory; no raw data or filesystem access."""
    if type(limit) is not int or not 1 <= limit <= 128:
        raise ValueError("invalid_capture_limit")
    with storage._lock, storage._connect() as db:
        rows = db.execute(
            "SELECT key,value FROM metadata WHERE key GLOB ? ORDER BY key LIMIT 2048",
            (_PREFIX + "*",)).fetchall()
    now = time.time() if now is None else now
    result = []
    for key, raw in rows:
        diagnostic = key[len(_PREFIX):]
        if not _SHA.fullmatch(diagnostic):
            continue
        try:
            record = json.loads(raw)
            if (record["capture_state"] in ("pending", "failed")
                    and record.get("retry_after", 0) <= now):
                result.append(diagnostic)
        except (ValueError, KeyError, TypeError):
            continue
        if len(result) == limit:
            break
    return tuple(result)


def resume_captures(storage, resolve_members, *, directory=None, limit=8):
    """Trusted local resolver only; call from maintenance worker, not GTK.

    Resolver returns authorized members or an empty dict when unavailable.
    Never calls a provider or fabricates original input.
    """
    if not callable(resolve_members):
        raise ValueError("invalid_capture_resolver")
    outcomes = []
    for diagnostic in pending_capture_ids(storage, limit=limit):
        try:
            members = resolve_members(diagnostic)
            if type(members) is not dict:
                raise ValueError("invalid_capture_members")
            state = export_capture(storage, diagnostic, members=members,
                                   directory=directory)
        except (OSError, ValueError):
            key = _PREFIX + diagnostic
            with storage._lock, storage._connect() as db:
                row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            state = "failed"
            if row is not None:
                record = json.loads(row[0])
                if record["capture_state"] in ("pending", "failed"):
                    _record_outcome(storage, key, row[0], record, state)
                else:
                    state = record["capture_state"]
        outcomes.append((diagnostic, state))
    return tuple(outcomes)


def capture_status(storage, diagnostic):
    """Read-only technical projection for an already authorized diagnostic ID.

    Caller must enforce the alert's scope before exposing this projection.
    No snapshots, input digest, filesystem path or raw bytes are returned.
    """
    if type(diagnostic) is not str or not _SHA.fullmatch(diagnostic):
        raise ValueError("invalid_capture_id")
    with storage._lock, storage._connect() as db:
        row = db.execute("SELECT value FROM metadata WHERE key=?",
                         (_PREFIX + diagnostic,)).fetchone()
    if row is None:
        return None
    record = json.loads(row[0])
    return {key: record[key] for key in
            ("capture_state", "attempts", "retry_after") if key in record}
