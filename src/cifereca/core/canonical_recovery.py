"""#3171: durable *completed-delivery* handoff between GET and SQLite B commit.

The legacy acquisition_ledger is an OPERATION JOURNAL, never ownership or a
replacement OriginLink. A journal row is created only AFTER final bytes, SHA
and an independent completed-delivery receipt are verified. On retry, the
exact currently admitted source/offer may reuse those bytes without a second
GET, but only through the same Core executor/session/consent/SQLite guards.
No blob is inferred from a matching filename, SHA or old backup_items row.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import time

from ..catalog import LocalCatalog
from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from .canonical_publication import _physical_target, _same_file, _stable
from .source_convergence import SourceKey

_SCHEMA = "canonical.completed-delivery.v1"


def _stage_id(source: SourceKey, offer_id: str) -> str:
    scope = source.scope
    return _stable(
        "canonical-intake", scope.identity_id, scope.provider_id,
        scope.credential_id, scope.account_id, source.branch,
        source.edition_id, source.right_id, source.kind, offer_id,
    )


def _scope(source: SourceKey) -> list[str]:
    scope = source.scope
    return [
        scope.identity_id, scope.provider_id, scope.credential_id,
        scope.account_id, source.branch, source.edition_id,
        source.right_id, source.kind,
    ]


def _lock_ok(lock: InterProcessLock) -> bool:
    return (
        type(lock) is InterProcessLock
        and lock._handle is not None
        and lock.path.resolve() == acquisition_lock_file().resolve()
    )


def _path_verified(storage, *, root: Path, source: SourceKey,
                   path: Path, final_sha256: str) -> Path:
    if (not isinstance(root, Path) or not isinstance(path, Path)
            or type(final_sha256) is not str or len(final_sha256) != 64
            or any(c not in "0123456789abcdef" for c in final_sha256)):
        raise ValueError("canonical_intake_invalid_path_or_hash")
    directories = [
        item.get("directory_name") for item in storage.identities()
        if item.get("identity_id") == source.scope.identity_id
    ]
    if len(directories) != 1 or type(directories[0]) is not str:
        raise ValueError("canonical_intake_profile_unavailable")
    actual = _physical_target(path, root, directories[0])
    before = os.stat(actual, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size == 0:
        raise ValueError("canonical_intake_invalid_final_bytes")
    fmt = LocalCatalog._media_format(actual)
    if fmt not in ("epub", "kepub", "pdf"):
        raise ValueError("canonical_intake_invalid_media_type")
    valid, _reason = LocalCatalog._validate(actual, fmt)
    if not valid or LocalCatalog.sha256(actual) != final_sha256 or not _same_file(actual, before):
        raise ValueError("canonical_intake_final_sha_not_verified")
    return actual


def record_completed_delivery_for_recovery(
    storage, *, source: SourceKey, offer_id: str, completed,
    library_root: Path, shared_lock: InterProcessLock,
) -> str:
    """Only Core calls this after an authorized, completed supplier delivery.

    The caller revalidates session/consent before and after this operation.
    SQLite still revalidates the owner's enabled credential and exact account,
    under BEGIN IMMEDIATE. This is a journal, not a successful acquisition.
    """
    # Lazy import prevents a cycle with the executor.
    from .convergence_execution import CompletedDelivery

    if (type(source) is not SourceKey or not _lock_ok(shared_lock)
            or type(offer_id) is not str or not offer_id
            or len(offer_id) > 1024
            or type(completed) is not CompletedDelivery
            or completed.source != source
            or completed.delivery_offer_id != offer_id
            or type(completed.receipt_ref) is not str or not completed.receipt_ref
            or len(completed.receipt_ref) > 1024
            or type(completed.adept_receipt_ref) is not str
            or len(completed.adept_receipt_ref) > 1024
            or any("\x00" in x or "\r" in x or "\n" in x for x in (
                offer_id, completed.receipt_ref, completed.adept_receipt_ref,
            ))):
        raise ValueError("canonical_intake_completed_offer_required")
    actual = _path_verified(
        storage, root=library_root, source=source,
        path=completed.path, final_sha256=completed.final_sha256,
    )
    scope = source.scope
    record_id = _stage_id(source, offer_id)
    stage = {
        "schema": _SCHEMA, "source": _scope(source),
        "offer_id": offer_id, "path": str(actual),
        "sha256": completed.final_sha256,
        "receipt": completed.receipt_ref,
        "adept_receipt": completed.adept_receipt_ref,
    }
    data = json.dumps(stage, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    now = int(time.time())
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        binding = db.execute(
            "SELECT identity_id,enabled FROM provider_connections "
            "WHERE provider_id=? AND credential_id=?",
            (scope.provider_id, scope.credential_id),
        ).fetchone()
        accounts = db.execute(
            "SELECT account_id FROM provider_accounts WHERE identity_id=? "
            "AND provider_id=? AND credential_id=?",
            (scope.identity_id, scope.provider_id, scope.credential_id),
        ).fetchall()
        if (binding != (scope.identity_id, 1)
                or sum(a[0] == scope.account_id for a in accounts) != 1):
            raise ValueError("canonical_intake_account_rebound")
        row = db.execute(
            "SELECT provider_id,credential_id,account_id,item_id,identity_id,"
            "status,metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (record_id,),
        ).fetchone()
        coords = (
            scope.provider_id, scope.credential_id, scope.account_id,
            source.edition_id, scope.identity_id,
        )
        if row is None:
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,provider_id,credential_id,account_id,item_id,"
                "identity_id,status,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (record_id, *coords, "acquiring", data, now, now),
            )
        elif row[:5] != coords:
            raise ValueError("canonical_intake_record_collision")
        elif row[5] == "complete":
            # A replayed current delivery must not alter a committed receipt.
            if row[6] != data:
                raise ValueError("canonical_intake_completed_record_immutable")
        elif row[5] == "acquiring":
            db.execute(
                "UPDATE acquisition_ledger SET metadata_json=?,updated_at=? "
                "WHERE acquisition_id=?", (data, now, record_id),
            )
        else:
            raise ValueError("canonical_intake_record_state_conflict")
    return record_id


def recover_completed_delivery(
    storage, *, source: SourceKey, offer_id: str,
    library_root: Path, shared_lock: InterProcessLock,
):
    """Read a previously persisted final receipt; never invent a provider GET.

    The executing Core Sync must have CURRENT positive A + offer + consent,
    and still perform its ordinary session/epoch revalidation and canonical B
    publication. Unavailable/contradictory journal state is treated as NO
    RECOVERY, not as local satisfaction or permission to download.
    """
    from .convergence_execution import CompletedDelivery

    if (type(source) is not SourceKey or not _lock_ok(shared_lock)
            or type(offer_id) is not str or not offer_id
            or len(offer_id) > 1024):
        return None
    record_id = _stage_id(source, offer_id)
    scope = source.scope
    with storage._lock, storage._connect() as db:
        row = db.execute(
            "SELECT provider_id,credential_id,account_id,item_id,identity_id,"
            "status,metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (record_id,),
        ).fetchone()
        if row is None or row[:5] != (
            scope.provider_id, scope.credential_id, scope.account_id,
            source.edition_id, scope.identity_id,
        ) or row[5] not in ("acquiring", "complete"):
            return None
        owner = db.execute(
            "SELECT identity_id,enabled FROM provider_connections "
            "WHERE provider_id=? AND credential_id=?",
            (scope.provider_id, scope.credential_id),
        ).fetchone()
        registered_accounts = db.execute(
            "SELECT account_id FROM provider_accounts WHERE identity_id=? "
            "AND provider_id=? AND credential_id=?",
            (scope.identity_id, scope.provider_id, scope.credential_id),
        ).fetchall()
        if owner != (scope.identity_id, 1) or sum(
            item[0] == scope.account_id for item in registered_accounts
        ) != 1:
            return None
        try:
            raw = json.loads(row[6])
            if (type(raw) is not dict or raw.get("schema") != _SCHEMA
                    or raw.get("source") != _scope(source)
                    or raw.get("offer_id") != offer_id
                    or type(raw.get("path")) is not str
                    or type(raw.get("sha256")) is not str
                    or type(raw.get("receipt")) is not str
                    or not raw["receipt"]
                    or type(raw.get("adept_receipt")) is not str):
                return None
            completed = CompletedDelivery(
                source=source, delivery_offer_id=offer_id,
                path=Path(raw["path"]), final_sha256=raw["sha256"],
                receipt_ref=raw["receipt"],
                adept_receipt_ref=raw["adept_receipt"],
            )
        except (ValueError, TypeError, KeyError, OSError):
            return None
    try:
        _path_verified(
            storage, root=library_root, source=source,
            path=completed.path, final_sha256=completed.final_sha256,
        )
    except (ValueError, OSError, RuntimeError):
        return None
    return completed


def mark_completed_delivery_published(
    storage, *, source: SourceKey, offer_id: str,
    artifact_id: str, shared_lock: InterProcessLock,
) -> None:
    """Best-effort operation journal status; canonical OriginLink is authority."""
    if (type(source) is not SourceKey or not _lock_ok(shared_lock)
            or type(offer_id) is not str
            or type(artifact_id) is not str
            or not artifact_id.startswith("sha256:")):
        raise ValueError("canonical_intake_invalid_published_transition")
    now = int(time.time())
    with storage._lock, storage._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET status='complete',artifact_id=?,"
            "updated_at=? WHERE acquisition_id=? AND status='acquiring'",
            (artifact_id, now, _stage_id(source, offer_id)),
        )


__all__ = [
    "record_completed_delivery_for_recovery",
    "recover_completed_delivery",
    "mark_completed_delivery_published",
]
