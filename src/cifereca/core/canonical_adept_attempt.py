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
        raw = storage.metadata_get(_key(source))
    except Exception:
        return result("unreadable")
    if raw is None:
        return result("none")
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
            or any(c not in "0123456789abcdef" for c in saved["operation_id"])):
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


def release_unsubmitted_attempt(storage, *, source: SourceKey, offer_id: str,
                                operation_id: str, shared_lock) -> None:
    """Release only the claim created by this still-live Core dispatch.

    Caller must know it has NOT entered execute_acquisition_request. This
    cannot qualify or release a historical marker after a crash, runtime call
    or unknown result. The exact operation ID is held only by its creator.
    """
    from .canonical_recovery import _lock_ok
    if not _lock_ok(shared_lock):
        raise ValueError("canonical_adept_attempt_core_lock_required")
    expected = _value(offer_id, operation_id)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            "DELETE FROM metadata WHERE key=? AND value=?",
            (_key(source), expected),
        )
        if cursor.rowcount != 1:
            raise ValueError("canonical_adept_attempt_changed")
