"""P0 #2847: restore a missing Placement with exact linked catalogue rows.

A file returned at its *original* location can be re-attested without
promoting its provider rights, importing a receipt or moving bytes. The
orphan-only operator stays separate and never accepts linked projections.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from .acquisition_audit import _file_proof
from .canonical_recovery import _lock_ok
from .library_declared_links import DeclarationRejected
from .missing_placement_retirement import _scope, _directory_signature
from .missing_placement_same_path import _blocked, _evidence

_TOKEN = re.compile(r"[0-9a-f]{64}\Z")


def preview_reappeared_linked_placement(
    storage, *, placement_id: str, identity_id: str, library_root: Path,
) -> dict[str, object]:
    """Read-only, scoped token for one exact missing Placement and catalogue."""
    scoped = _scope(library_root, placement_id, identity_id)
    if scoped is None:
        return _blocked("invalid_or_unavailable_scope")
    root, _ = scoped
    try:
        with storage._lock, storage._connect() as db:
            db.execute("SAVEPOINT linked_reappeared_preview")
            try:
                result, evidence = _evidence(
                    db, root=root, identity_id=identity_id,
                    placement_id=placement_id, linked_catalogue=True,
                )
            finally:
                db.execute("RELEASE SAVEPOINT linked_reappeared_preview")
        if evidence is None:
            return result
        return {**result, "plan_token": evidence[0],
                "catalogue_rows": len(evidence[-1]),
                "requires_confirmation": True}
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError,
            RecursionError, DeclarationRejected):
        return _blocked("evidence_unavailable")


def restore_reappeared_linked_placement(
    storage, *, placement_id: str, identity_id: str, library_root: Path,
    plan_token: str, confirmed: bool, shared_lock,
) -> dict[str, object]:
    """Atomically restore only local presence; no right/source/bytes mutation."""
    if confirmed is not True:
        return _blocked("confirmation_required")
    if type(plan_token) is not str or _TOKEN.fullmatch(plan_token) is None:
        return _blocked("invalid_plan_token")
    if not _lock_ok(shared_lock):
        return _blocked("acquisition_lock_required")
    scoped = _scope(library_root, placement_id, identity_id)
    if scoped is None:
        return _blocked("invalid_or_unavailable_scope")
    root, original_root = scoped
    try:
        with storage._lock:
            db = storage._connect()
            old_sync = int(db.execute("PRAGMA synchronous").fetchone()[0])
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                result, evidence = _evidence(
                    db, root=root, identity_id=identity_id,
                    placement_id=placement_id, linked_catalogue=True,
                )
                if evidence is None:
                    db.rollback()
                    return result
                token, path, updated, metadata, signature, dev, ino, linked = evidence
                if token != plan_token:
                    db.rollback()
                    return _blocked("stale_plan")
                cur = db.execute(
                    "UPDATE placements SET state='present',"
                    "updated_at=MAX(updated_at+1,CAST(strftime('%s','now') AS INTEGER)) "
                    "WHERE placement_id=? AND identity_id=? AND path=? "
                    "AND state='missing' AND updated_at=? AND metadata_json=?",
                    (placement_id, identity_id, path, updated, metadata),
                )
                if cur.rowcount != 1:
                    db.rollback()
                    return _blocked("stale_plan")
                for cid, art, owner, provider, item, state, raw, prior in linked:
                    cur = db.execute(
                        "UPDATE catalog_entries SET local_state='present',"
                        "updated_at=MAX(updated_at+1,CAST(strftime('%s','now') AS INTEGER)) "
                        "WHERE catalog_entry_id=? AND placement_id=? "
                        "AND identity_id=? AND artifact_id=? "
                        "AND provider_id IS ? AND item_id IS ? "
                        "AND local_state='missing' AND updated_at=? "
                        "AND metadata_json=?",
                        (cid, placement_id, identity_id, art, provider, item,
                         prior, raw),
                    )
                    if cur.rowcount != 1:
                        db.rollback()
                        return _blocked("stale_plan")
                current = os.stat(root, follow_symlinks=False)
                owner_row = db.execute(
                    "SELECT directory_name FROM identities WHERE identity_id=?",
                    (identity_id,),
                ).fetchone()
                if (owner_row is None
                        or (current.st_dev, current.st_ino) != (dev, ino)
                        or (original_root.st_dev, original_root.st_ino) != (dev, ino)
                        or _directory_signature(root / owner_row[0]) != signature
                        or _file_proof(
                            root=root, directory=owner_row[0], path=path,
                            artifact_id=db.execute(
                                "SELECT artifact_id FROM placements "
                                "WHERE placement_id=?", (placement_id,),
                            ).fetchone()[0],
                        ) != "verified_sha256"):
                    db.rollback()
                    return _blocked("file_changed")
                db.commit()
                return {"status": "restored", "reason": "linked_catalogue_reappeared",
                        "catalogue_rows": len(linked)}
            except Exception:
                db.rollback()
                raise
            finally:
                db.execute(f"PRAGMA synchronous={old_sync}")
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError,
            RecursionError, DeclarationRejected):
        return _blocked("transaction_failed")


__all__ = [
    "preview_reappeared_linked_placement",
    "restore_reappeared_linked_placement",
]
