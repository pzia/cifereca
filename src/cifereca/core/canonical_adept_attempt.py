"""#3197: durable negative-only fence around one-use loan fulfillment.

No marker grants permission, proves B or retains ACSM bytes. Once the Core
starts ADEPT, only its independently verified completed-delivery checkpoint
may release this automatic retry fence. A crash or ambiguous result leaves
a hold for explicit intervention; a changed offer ID cannot revive it.
"""
from __future__ import annotations

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
    return json.dumps({
        "schema": 1, "offer_digest": hashlib.sha256(offer_id.encode()).hexdigest(),
        "operation_id": operation_id,
    }, sort_keys=True, separators=(",", ":"))


def pending_loan_adept_attempt(storage, source: SourceKey) -> bool:
    if source.kind != "loan":
        return False
    try:
        # Even malformed/empty stored values represent an unresolved hold.
        return storage.metadata_get(_key(source)) is not None
    except Exception:
        return True


def begin_loan_adept_attempt(storage, *, source: SourceKey, offer_id: str,
                              shared_lock) -> str:
    from .canonical_recovery import _exact_borrower_bound_in_db, _lock_ok
    if not _lock_ok(shared_lock):
        raise ValueError("canonical_adept_attempt_core_lock_required")
    key = _key(source)
    operation = secrets.token_hex(16)
    value = _value(offer_id, operation)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
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
    expected = _value(offer_id, operation_id)
    cursor = db.execute(
        "DELETE FROM metadata WHERE key=? AND value=?",
        (_key(source), expected),
    )
    if cursor.rowcount != 1:
        raise ValueError("canonical_adept_attempt_changed")
