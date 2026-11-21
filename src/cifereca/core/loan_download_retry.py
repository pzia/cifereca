"""#7161: negative-only budget for automatic content transfer failures of an exact resource.

A budget is not rights evidence, consent, a DRM replay permit or a receipt.
The existing canonical acquisition lock serializes writes across processes.
Never clear an ADEPT attempt, a historical ledger row or any physical file.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import time

from .canonical_publication import _stable
from .canonical_recovery import (
    _exact_borrower_bound_in_db, _lock_ok, _unique_checkpoint_fields,
)
from .source_convergence import SourceKey

FAILURE_LIMIT = 3
_LOAN_PREFIX = "canonical.loan_download_retry.v1"
_OWNED_KEY_PREFIX = "canonical.download_retry.v1:"
_INTERVENTION_PREFIX = "canonical.download_intervention.v1:"
_REASONS = frozenset({"canonical_delivery_failed", "drm_unresolved"})


@dataclass(frozen=True, slots=True)
class LoanDownloadRetry:
    failures: int = 0
    last_reason: str = ""
    last_failed_at: int = 0

    @property
    def paused(self) -> bool:
        return self.failures >= FAILURE_LIMIT


def _source_coordinates(source: SourceKey) -> tuple[str, ...]:
    scope = source.scope
    return (scope.identity_id, scope.provider_id, scope.credential_id,
            scope.account_id, source.branch, source.edition_id, source.right_id)


def _intervention_key(source: SourceKey) -> str:
    return _INTERVENTION_PREFIX + _stable("source", *_source_coordinates(source), source.kind)


def read_adept_intervention(db, source: SourceKey) -> int:
    row = db.execute(
        "SELECT CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND 12 "
        "THEN value ELSE NULL END FROM metadata WHERE key=?",
        (_intervention_key(source),),
    ).fetchone()
    if row is None:
        return 0
    raw = row[0]
    if type(raw) is str and raw.isascii() and raw.isdecimal() and len(raw) <= 12:
        date = int(raw)
        if 0 < date <= 253402300799:
            return date
    raise ValueError("download_intervention_state_invalid")


@contextmanager
def _retry_connection(storage):
    """Own only a fresh worker transaction, never settle caller-owned work."""
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("loan_download_retry_nested_transaction")
        with db:
            yield db


def record_adept_intervention(storage, source: SourceKey, *, shared_lock) -> None:
    if not _lock_ok(shared_lock):
        raise ValueError("loan_download_retry_lock_required")
    with _retry_connection(storage) as db:
        db.execute("BEGIN IMMEDIATE")
        if not _exact_borrower_bound_in_db(db, source.scope):
            raise ValueError("loan_download_retry_source_rebound")
        db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_intervention_key(source), str(int(time.time()))),
        )


def _key(source: SourceKey) -> str:
    if type(source) is not SourceKey:
        raise ValueError("download_retry_invalid_source")
    coordinates = _source_coordinates(source)
    if source.kind == "loan":
        return _stable(_LOAN_PREFIX, *coordinates)
    # Match the durable source reference key so the queue can select failed
    # purchases without loading a complete owned library into Python.
    return _OWNED_KEY_PREFIX + _stable("source", *coordinates, source.kind)


def _decode(raw) -> LoanDownloadRetry:
    if raw is None:
        return LoanDownloadRetry()
    try:
        if type(raw) is not str or not 0 < len(raw) <= 1024:
            raise ValueError()
        data = json.loads(raw, object_pairs_hook=_unique_checkpoint_fields)
        if (type(data) is not dict
                or set(data) != {"schema", "failures", "last_reason", "last_failed_at"}
                or type(data["schema"]) is not int or data["schema"] != 1
                or type(data["failures"]) is not int
                or not 1 <= data["failures"] <= FAILURE_LIMIT
                or type(data["last_reason"]) is not str
                or data["last_reason"] not in _REASONS
                or type(data["last_failed_at"]) is not int
                or not 0 < data["last_failed_at"] <= 253402300799):
            raise ValueError()
        return LoanDownloadRetry(data["failures"], data["last_reason"],
                                 data["last_failed_at"])
    except (ValueError, TypeError, RecursionError):
        raise ValueError("loan_download_retry_state_invalid") from None


def _read_retry_in_db(db, source: SourceKey) -> LoanDownloadRetry:
    row = db.execute(
        "SELECT CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND 1024 "
        "THEN value ELSE NULL END FROM metadata WHERE key=?",
        (_key(source),),
    ).fetchone()
    if row is None:
        return LoanDownloadRetry()
    if type(row[0]) is not str:
        raise ValueError("loan_download_retry_state_invalid")
    return _decode(row[0])


def read_loan_download_retry(storage, source: SourceKey) -> LoanDownloadRetry:
    """Read one exact row; a corrupt row never licenses another delivery."""
    with _retry_connection(storage) as db:
        return _read_retry_in_db(db, source)


def record_loan_download_failure(storage, source: SourceKey, *, reason: str,
                                 shared_lock) -> LoanDownloadRetry:
    """Only a failed delivery call, not an inventory miss or denied consent."""
    if not _lock_ok(shared_lock):
        raise ValueError("loan_download_retry_lock_required")
    if type(reason) is not str or reason not in _REASONS:
        raise ValueError("loan_download_retry_invalid_reason")
    key = _key(source)
    with _retry_connection(storage) as db:
        db.execute("BEGIN IMMEDIATE")
        if not _exact_borrower_bound_in_db(db, source.scope):
            raise ValueError("loan_download_retry_source_rebound")
        previous = _read_retry_in_db(db, source)
        if previous.paused:
            return previous
        status = LoanDownloadRetry(previous.failures + 1, reason, int(time.time()))
        db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps({
                "schema": 1, "failures": status.failures,
                "last_reason": status.last_reason, "last_failed_at": status.last_failed_at,
            }, sort_keys=True, separators=(",", ":"))),
        )
        return status


def clear_loan_download_retry(storage, source: SourceKey, *, shared_lock) -> None:
    """Clear the download queue entry after verifying the exact local B.

    The acquisition ledger and pending ADEPT attempts have their own
    verification and transition rules.
    """
    if not _lock_ok(shared_lock):
        raise ValueError("loan_download_retry_lock_required")
    keys = (_key(source), _intervention_key(source))
    with _retry_connection(storage) as db:
        # Verified B is common on opening Sync. Avoid writes for resources
        # without a recorded download failure.
        if db.execute("SELECT 1 FROM metadata WHERE key IN (?, ?) LIMIT 1", keys).fetchone() is None:
            return
        db.execute("BEGIN IMMEDIATE")
        if not _exact_borrower_bound_in_db(db, source.scope):
            raise ValueError("loan_download_retry_source_rebound")
        db.execute("DELETE FROM metadata WHERE key IN (?, ?)", keys)
