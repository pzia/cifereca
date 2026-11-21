"""#7161: negative-only budget for automatic delivery failures of an exact loan.

A budget is not rights evidence, consent, a DRM replay permit or a receipt.
The existing canonical acquisition lock serializes writes across processes.
Never clear an ADEPT attempt, a historical ledger row or any physical file.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time

from .canonical_publication import _stable
from .canonical_recovery import (
    _exact_borrower_bound_in_db, _lock_ok, _unique_checkpoint_fields,
)
from .source_convergence import SourceKey

FAILURE_LIMIT = 3
_PREFIX = "canonical.loan_download_retry.v1"
_REASONS = frozenset({"canonical_delivery_failed", "drm_unresolved"})


@dataclass(frozen=True, slots=True)
class LoanDownloadRetry:
    failures: int = 0
    last_reason: str = ""
    last_failed_at: int = 0

    @property
    def paused(self) -> bool:
        return self.failures >= FAILURE_LIMIT


def _key(source: SourceKey) -> str:
    if type(source) is not SourceKey or source.kind != "loan":
        raise ValueError("loan_download_retry_invalid_source")
    scope = source.scope
    return _stable(_PREFIX, scope.identity_id, scope.provider_id,
                   scope.credential_id, scope.account_id, source.branch,
                   source.edition_id, source.right_id)


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


def read_loan_download_retry(storage, source: SourceKey) -> LoanDownloadRetry:
    """Read one exact row; a corrupt row never licenses another delivery."""
    return _decode(storage.metadata_get(_key(source)))


def record_loan_download_failure(storage, source: SourceKey, *, reason: str,
                                 shared_lock) -> LoanDownloadRetry:
    """Only a failed delivery call, not an inventory miss or denied consent."""
    if not _lock_ok(shared_lock):
        raise ValueError("loan_download_retry_lock_required")
    if type(reason) is not str or reason not in _REASONS:
        raise ValueError("loan_download_retry_invalid_reason")
    key = _key(source)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not _exact_borrower_bound_in_db(db, source.scope):
            raise ValueError("loan_download_retry_source_rebound")
        row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        previous = _decode(row[0] if row is not None else None)
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
    """Forget only this budget after verified B (or a future explicit reset).

    Callers must independently attest the verified B or exact user action.
    In particular, this cannot clear a pending ADEPT attempt or any other
    negative admission evidence, even if the exact source is the same.
    """
    if not _lock_ok(shared_lock):
        raise ValueError("loan_download_retry_lock_required")
    key = _key(source)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not _exact_borrower_bound_in_db(db, source.scope):
            raise ValueError("loan_download_retry_source_rebound")
        db.execute("DELETE FROM metadata WHERE key=?", (key,))
