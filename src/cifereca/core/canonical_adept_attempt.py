"""#3197: durable negative-only fence around one-use loan fulfillment.

No marker grants permission, proves B or retains ACSM bytes. Once the Core
starts ADEPT, only its independently verified completed-delivery checkpoint
may release this automatic retry fence. A crash or ambiguous result leaves
a hold for explicit intervention; a changed offer ID cannot revive it.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import secrets

from .canonical_publication import _stable
from .source_convergence import SourceKey

_PREFIX = "canonical.adept_attempt.v1"
_UNSUBMITTED_PREFIX = "canonical.adept_unsubmitted.v1"
_UNSUBMITTED_REASONS = ("core_pre_dispatch_refusal", "runtime_attested_not_submitted")


def _key(source: SourceKey) -> str:
    if type(source) is not SourceKey or source.kind != "loan":
        raise ValueError("canonical_adept_attempt_invalid_source")
    s = source.scope
    return _stable(_PREFIX, s.identity_id, s.provider_id, s.credential_id,
                   s.account_id, source.branch, source.edition_id, source.right_id)


def _unsubmitted_key(source):
    return _stable(_UNSUBMITTED_PREFIX, _key(source))


def _unsubmitted_value(source, marker, reason):
    if _decode_marker(marker) is None or reason not in _UNSUBMITTED_REASONS:
        raise ValueError("canonical_adept_unsubmitted_invalid")
    return json.dumps({
        "schema": 1,
        "marker_fingerprint": _stable("canonical.adept_marker_review.v1", _key(source), marker),
        "reason": reason,
    }, sort_keys=True, separators=(",", ":"))


def _unsubmitted_row(db, source):
    return db.execute(
        "SELECT CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND 1024 "
        "THEN value ELSE NULL END FROM metadata WHERE key=?",
        (_unsubmitted_key(source),),
    ).fetchone()


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
        return _marker_row(db, source)


def _marker_row(db, source):
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
        with storage._lock:
            db = storage._connect()
            if db.in_transaction:
                return True
            return (_marker_row(db, source) is not None
                    or _unsubmitted_row(db, source) is not None)
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
    saved = _decode_marker(row[0])
    if saved is None:
        return result("held_invalid")
    digest = hashlib.sha256(offer_id.encode()).hexdigest()
    return result("held_same_offer" if saved["offer_digest"] == digest
                  else "held_other_offer")


def _decode_marker(raw):
    if type(raw) is not str or not 0 < len(raw) <= 8192:
        return None
    try:
        saved = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return None
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
        return None
    return saved


@dataclass(frozen=True, slots=True)
class LoanAdeptMarkerReview:
    """Private negative-only witness; never exposes the operation or offer."""
    state: str
    fingerprint: str = field(default="", repr=False)

    @property
    def unlock_authorized(self) -> bool:
        return False


def _marker_review(row, source):
    if row is None:
        return LoanAdeptMarkerReview("none")
    if _decode_marker(row[0]) is None:
        return LoanAdeptMarkerReview("held_invalid")
    # Bind the exact canonical TEXT to every scope coordinate via its key.
    # No fingerprint is produced for invalid, ambiguous or missing values.
    fingerprint = _stable("canonical.adept_marker_review.v1", _key(source), row[0])
    return LoanAdeptMarkerReview("held_phase_unknown", fingerprint)


def review_loan_adept_marker(storage, source: SourceKey) -> LoanAdeptMarkerReview:
    """Select one exact v1 marker for private review, even with no fresh offer.

    No historical phase, current activation, or right is inferred. The witness
    is not a credential and must never be interpreted as an unlock permit.
    """
    if type(source) is not SourceKey or source.kind != "loan":
        return LoanAdeptMarkerReview("invalid_source")
    try:
        return _marker_review(_read_marker(storage, source), source)
    except Exception:
        return LoanAdeptMarkerReview("unreadable")


def review_loan_adept_marker_in_transaction(db, source: SourceKey) -> LoanAdeptMarkerReview:
    """Negative witness in an existing review snapshot, never write authority."""
    if type(source) is not SourceKey or source.kind != "loan":
        return LoanAdeptMarkerReview("invalid_source")
    if not db.in_transaction:
        return LoanAdeptMarkerReview("unreadable")
    try:
        return _marker_review(_marker_row(db, source), source)
    except Exception:
        return LoanAdeptMarkerReview("unreadable")


def matches_reviewed_loan_adept_marker_in_transaction(db, *, source, review) -> bool:
    """Compare the reviewed negative fact within a caller-owned SQL snapshot.

    A later supervisor must independently prove activation, permission, safe
    replay and local recovery precedence under the acquisition lock. This
    helper does not acquire that lock, mutate SQLite or establish authority.
    """
    if (not db.in_transaction or type(review) is not LoanAdeptMarkerReview
            or review.state != "held_phase_unknown" or not review.fingerprint
            or type(source) is not SourceKey or source.kind != "loan"):
        return False
    try:
        return _marker_review(_marker_row(db, source), source) == review
    except Exception:
        return False


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
        if _unsubmitted_row(db, source) is not None:
            raise ValueError("canonical_adept_unsubmitted_pending")
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
    if _unsubmitted_row(db, source) is not None:
        raise ValueError("canonical_adept_unsubmitted_conflict")
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


@dataclass(frozen=True, slots=True)
class LoanAdeptUnsubmittedReview:
    """Exact local evidence only; no right, activation or replay authority."""
    state: str
    reason: str = ""
    marker_fingerprint: str = field(default="", repr=False)

    @property
    def unlock_authorized(self):
        return False


def review_loan_adept_unsubmitted_in_transaction(db, *, source):
    if not db.in_transaction or type(source) is not SourceKey or source.kind != "loan":
        return LoanAdeptUnsubmittedReview("unreadable")
    try:
        witness = _unsubmitted_row(db, source)
        if witness is None:
            return LoanAdeptUnsubmittedReview("not_recorded")
        marker = _marker_row(db, source)
        reviewed = _marker_review(marker, source)
        if reviewed.state != "held_phase_unknown":
            return LoanAdeptUnsubmittedReview("unverified")
        for reason in _UNSUBMITTED_REASONS:
            if witness == (_unsubmitted_value(source, marker[0], reason),):
                return LoanAdeptUnsubmittedReview(
                    "non_submission_recorded", reason, reviewed.fingerprint)
        return LoanAdeptUnsubmittedReview("unverified")
    except Exception:
        return LoanAdeptUnsubmittedReview("unreadable")


def review_loan_adept_unsubmitted(storage, *, source):
    """Read marker and witness in one SQLite snapshot, after restart too."""
    try:
        with storage._lock:
            db = storage._connect()
            if db.in_transaction:
                return LoanAdeptUnsubmittedReview("unreadable")
            with db:
                db.execute("BEGIN")
                return review_loan_adept_unsubmitted_in_transaction(db, source=source)
    except Exception:
        return LoanAdeptUnsubmittedReview("unreadable")


def _consume_proven_unsubmitted(storage, *, source, expected, witness):
    with _durable_marker_transaction(storage) as db:
        if (_marker_row(db, source) != (expected,)
                or _unsubmitted_row(db, source) != (witness,)):
            raise ValueError("canonical_adept_attempt_changed")
        for key, value in ((_key(source), expected), (_unsubmitted_key(source), witness)):
            deleted = db.execute("DELETE FROM metadata WHERE key=? AND value=?", (key, value))
            if deleted.rowcount != 1:
                raise ValueError("canonical_adept_attempt_changed")


def release_proven_unsubmitted_attempt(storage, *, source, offer_id, operation_id,
                                       shared_lock, reason):
    """Record the already-live dispatch's actual refusal before consuming it.

    FIRST commit exact evidence with synchronous FULL. THEN consume both
    rows atomically under the same acquisition lock. A crash between commits
    retains the original fence and correlated evidence. This deliberately
    does not authorize a later operator to replay anything. Normal success
    leaves no extra row; old markers are not retrospectively upgraded.
    """
    from .canonical_recovery import _lock_ok
    if not _lock_ok(shared_lock):
        raise ValueError("canonical_adept_attempt_core_lock_required")
    expected = _value(offer_id, operation_id)
    witness = _unsubmitted_value(source, expected, reason)
    with _durable_marker_transaction(storage) as db:
        if _marker_row(db, source) != (expected,):
            raise ValueError("canonical_adept_attempt_changed")
        inserted = db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING",
            (_unsubmitted_key(source), witness),
        )
        if inserted.rowcount != 1:
            raise ValueError("canonical_adept_unsubmitted_pending")
    _consume_proven_unsubmitted(storage, source=source, expected=expected, witness=witness)


# Owner-supervised historical release uses the SAME canonical marker writer.
# The immutable audit survives subsequent attempts; the scoped index is only
# a negative profile constraint on the ordinary next Sync, never consent.
def historical_rearm_audit_key(review):
    return _stable("canonical.adept_historical_rearm.v1", review.fingerprint)


def _historical_rearm_index(source):
    return _stable("canonical.adept_historical_rearm_index.v1", _key(source))


def _is_digest(value):
    return (type(value) is str and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def historical_user_digest(value):
    if value is None or value == "":
        return ""
    if type(value) is not str or not 0 < len(value) <= 256:
        raise ValueError("historical_rearm_profile_unverified")
    return hashlib.sha256(value.encode()).hexdigest()


def _historical_rearm_binding_in_db(db, source):
    from .canonical_recovery import _scope, _unique_checkpoint_fields

    index = db.execute(
        "SELECT CASE WHEN length(CAST(value AS BLOB))<=128 THEN value ELSE NULL END "
        "FROM metadata WHERE key=?", (_historical_rearm_index(source),),
    ).fetchone()
    if index is None:
        return None
    key = index[0]
    if type(key) is not str or not key.startswith("canonical.adept_historical_rearm.v1:"):
        raise ValueError("historical_rearm_audit_unverified")
    row = db.execute(
        "SELECT CASE WHEN length(CAST(value AS BLOB))<=65536 THEN value ELSE NULL END "
        "FROM metadata WHERE key=?", (key,),
    ).fetchone()
    try:
        data = json.loads(row[0], object_pairs_hook=_unique_checkpoint_fields)
        fingerprint = data["marker_fingerprint"]
        borrower = data["borrower_identity_id"]
        profile = data["adept_profile_id"]
        user = data["user_uuid_digest"]
        if (type(data) is not dict or data["schema"] != 1
                or data["source"] != _scope(source)
                or data["result"] != "exact_marker_released"
                or data["proof"] != "owner_attested_same_activation_and_pre_dispatch_refusal"
                or not _is_digest(data["journal_sha256"])
                or not _is_digest(fingerprint)
                or key != _stable("canonical.adept_historical_rearm.v1", fingerprint)
                or not all(type(v) is str and 0 < len(v) <= 256 for v in (borrower, profile))
                or (user != "" and not _is_digest(user))):
            raise ValueError()
        return borrower, profile, user
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, RecursionError):
        raise ValueError("historical_rearm_audit_unverified") from None


def read_historical_rearm_binding(storage, source):
    """A durable negative profile constraint, not permission or proof of B."""
    if source.kind != "loan":
        return None
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("canonical_adept_attempt_nested_transaction")
        with db:
            db.execute("BEGIN")
            return _historical_rearm_binding_in_db(db, source)


def _release_reviewed_historical_attempt_in_transaction(
    db, *, source, review, witness, payload,
):
    """Internal CAS only: the supervisor owns fresh right/consent/profile/lease."""
    if (not matches_reviewed_loan_adept_marker_in_transaction(db, source=source, review=review)
            or review_loan_adept_unsubmitted_in_transaction(db, source=source) != witness
            or witness.state not in ("not_recorded", "non_submission_recorded")):
        raise ValueError("historical_rearm_marker_changed")
    previous_binding = _historical_rearm_binding_in_db(db, source)
    raw = _marker_row(db, source)
    proof = _unsubmitted_row(db, source)
    audit_key = historical_rearm_audit_key(review)
    db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (audit_key, payload))
    db.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_historical_rearm_index(source), audit_key),
    )
    new_binding = _historical_rearm_binding_in_db(db, source)
    if previous_binding is not None and previous_binding != new_binding:
        raise ValueError("historical_rearm_profile_changed")
    deleted = db.execute("DELETE FROM metadata WHERE key=? AND value=?", (_key(source), raw[0]))
    if deleted.rowcount != 1:
        raise ValueError("historical_rearm_marker_changed")
    if proof is not None:
        deleted = db.execute("DELETE FROM metadata WHERE key=? AND value=?",
                             (_unsubmitted_key(source), proof[0]))
        if deleted.rowcount != 1:
            raise ValueError("historical_rearm_marker_changed")
