"""#3197: durable negative-only fence around one-use loan fulfillment.

No marker grants permission, proves B or retains ACSM bytes. Once the Core
starts ADEPT, only its independently verified completed-delivery checkpoint
may release this automatic retry fence. A crash or ambiguous result leaves
a hold for explicit intervention; a changed offer ID cannot revive it.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import secrets

from .canonical_publication import _stable
from .source_convergence import SourceKey

_PREFIX = "canonical.adept_attempt.v1"


def _key(source: SourceKey) -> str:
    if type(source) is not SourceKey or source.kind != "loan":
        raise ValueError("canonical_adept_attempt_invalid_source")
    s = source.scope
    return _stable(_PREFIX, s.identity_id, s.provider_id, s.credential_id,
                   s.account_id, source.branch, source.edition_id, source.right_id)


def _value(offer_id: str, operation_id: str) -> str:
    if (type(offer_id) is not str or not offer_id
            or type(operation_id) is not str or len(operation_id) != 32
            or any(ch not in "0123456789abcdef" for ch in operation_id)):
        raise ValueError("canonical_adept_attempt_invalid_binding")
    return _value_from_digest(hashlib.sha256(offer_id.encode()).hexdigest(), operation_id)


def _value_from_digest(digest, operation_id):
    return json.dumps({
        "schema": 1, "offer_digest": digest, "operation_id": operation_id,
    }, sort_keys=True, separators=(",", ":"))


def _read_marker(storage, source):
    # Preserve the distinction between a missing row and an invalid value.
    # Never borrow the caller's transaction via metadata_get's context.
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("canonical_adept_attempt_nested_transaction")
        return db.execute(
            "SELECT CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND 8192 "
            "THEN value ELSE NULL END FROM metadata WHERE key=?", (_key(source),),
        ).fetchone()


@contextmanager
def _durable_marker_transaction(storage):
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("canonical_adept_attempt_nested_transaction")
        previous = db.execute("PRAGMA synchronous").fetchone()[0]
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.execute(f"PRAGMA synchronous={int(previous)}")


def pending_loan_adept_attempt(storage, source: SourceKey) -> bool:
    if source.kind != "loan":
        return False
    try:
        # Even malformed/empty stored values represent an unresolved hold.
        return _read_marker(storage, source) is not None
    except Exception:
        return True


def describe_loan_adept_attempt(storage, source: SourceKey, *, offer_id: str) -> dict[str, object]:
    """Read-only negative evidence for ONE exact loan, never replay authority.

    A legacy v1 marker records the offer digest and operation identity, not
    whether a DRM subprocess was actually started. Even a same-offer marker
    MUST NOT become a token-consumption or reset permit (#7161).
    """
    def result(state: str) -> dict[str, object]:
        return {"state": state, "unlock_authorized": False}

    if type(source) is not SourceKey or source.kind != "loan":
        return result("invalid_source")
    if type(offer_id) is not str or not offer_id or len(offer_id) > 2048:
        return result("invalid_offer")
    try:
        row = _read_marker(storage, source)
    except Exception:
        return result("unreadable")
    if row is None:
        return result("none")
    raw = row[0]
    # No raw marker, operation ID, token or source coordinates in the report.
    if type(raw) is not str or not 0 < len(raw) <= 8192:
        return result("held_invalid")
    try:
        saved = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return result("held_invalid")
    if (type(saved) is not dict or set(saved) != {
            "schema", "offer_digest", "operation_id"}
            or type(saved["schema"]) is not int or saved["schema"] != 1
            or type(saved["offer_digest"]) is not str
            or len(saved["offer_digest"]) != 64
            or any(c not in "0123456789abcdef" for c in saved["offer_digest"])
            or type(saved["operation_id"]) is not str
            or len(saved["operation_id"]) != 32
            or any(c not in "0123456789abcdef" for c in saved["operation_id"])
            or _value_from_digest(saved["offer_digest"], saved["operation_id"]) != raw):
        return result("held_invalid")
    digest = hashlib.sha256(offer_id.encode()).hexdigest()
    return result("held_same_offer" if saved["offer_digest"] == digest
                  else "held_other_offer")


def begin_loan_adept_attempt(storage, *, source: SourceKey, offer_id: str,
                              shared_lock) -> str:
    from .canonical_recovery import _exact_borrower_bound_in_db, _lock_ok
    if not _lock_ok(shared_lock):
        raise ValueError("canonical_adept_attempt_core_lock_required")
    key = _key(source)
    operation = secrets.token_hex(16)
    value = _value(offer_id, operation)
    with _durable_marker_transaction(storage) as db:
        if not _exact_borrower_bound_in_db(db, source.scope):
            raise ValueError("canonical_adept_attempt_source_rebound")
        cursor = db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
            (key, value),
        )
        if cursor.rowcount != 1:
            raise ValueError("canonical_adept_attempt_pending")
    return operation


def release_completed_attempt_in_transaction(db, *, source: SourceKey,
                                              offer_id: str, operation_id: str) -> None:
    """Called only in the verified completed-intake transaction, not on GET."""
    if not operation_id:
        return
    if not db.in_transaction:
        raise ValueError("canonical_adept_attempt_release_invalid")
    expected = _value(offer_id, operation_id)
    cursor = db.execute(
        "DELETE FROM metadata WHERE key=? AND value=?",
        (_key(source), expected),
    )
    if cursor.rowcount != 1:
        raise ValueError("canonical_adept_attempt_changed")


def release_unsubmitted_attempt(storage, *, source: SourceKey, offer_id: str,
                                operation_id: str, shared_lock) -> None:
    """Release only the claim created by this still-live Core dispatch.

    The caller either refused locally before dispatch or received the
    installed runtime's synchronous proof of non-submission. A crash or
    ambiguous result never permits this. The exact operation ID is held
    only by its creator.
    """
    from .canonical_recovery import _lock_ok
    if not _lock_ok(shared_lock):
        raise ValueError("canonical_adept_attempt_core_lock_required")
    _value(offer_id, operation_id)  # Non-submission requires a real exact claim.
    with _durable_marker_transaction(storage) as db:
        release_completed_attempt_in_transaction(
            db, source=source, offer_id=offer_id, operation_id=operation_id,
        )
