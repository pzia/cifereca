"""Independent installation preference for descriptive purchase enrichment.

The default is ON for qualified producers only. No change connects a plugin,
runs Sync, dispatches HTTP, deletes metadata or grants acquisition. A durable
revision makes OFF→ON different from the originally captured permission.
"""
from dataclasses import dataclass

from . import notice_c_job_journal as journal
from .notice_c_purchase_intent import _POLICY
from .source_enablement import source_enabled

_REVISION = _POLICY + ".revision"


@dataclass(frozen=True, slots=True)
class PurchasePolicy:
    enabled: bool
    revision: int


def _read_policy_txn(db):
    raw = db.execute("SELECT value FROM metadata WHERE key=?", (_POLICY,)).fetchone()
    rev = db.execute("SELECT value FROM metadata WHERE key=?", (_REVISION,)).fetchone()
    if rev is None:
        generation = 0
    else:
        value = rev[0]
        if (type(value) is not str or not value.isascii() or not value.isdecimal()
                or len(value) > 19 or not journal._integer(int(value), minimum=1)
                or str(int(value)) != value):
            raise ValueError("notice_purchase_policy_corrupt")
        generation = int(value)
    # Reading Settings must not install a producer or materialize a default.
    return PurchasePolicy(source_enabled(raw[0] if raw else None), generation)


def read_purchase_policy(storage):
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        return _read_policy_txn(db)


def change_purchase_policy(service, *, expected_storage, expected, enabled):
    """Explicit CAS under the same source context fence as live admission."""
    if (type(expected) is not PurchasePolicy or type(enabled) is not bool
            or type(expected.enabled) is not bool or not journal._integer(expected.revision)
            or not hasattr(service, "_source_context_lock")):
        raise ValueError("notice_purchase_policy_invalid")
    lock = service._source_context_lock
    if not lock.acquire(blocking=False):
        raise ValueError("notice_purchase_policy_busy")
    try:
        if service.storage is not expected_storage:
            raise ValueError("notice_purchase_policy_changed")
        with expected_storage._lock, expected_storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = _read_policy_txn(db)
            if current != expected:
                raise ValueError("notice_purchase_policy_changed")
            if current.enabled is enabled:
                return current
            revision = current.revision + 1
            if not journal._integer(revision, minimum=1):
                raise ValueError("notice_purchase_policy_corrupt")
            for key, value in ((_POLICY, "true" if enabled else "false"), (_REVISION, str(revision))):
                db.execute("INSERT INTO metadata(key,value) VALUES(?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            return PurchasePolicy(enabled, revision)
    finally:
        lock.release()
