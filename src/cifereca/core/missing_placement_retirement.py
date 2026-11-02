"""Explicit SQLite-only retirement of an unreferenced missing Placement (#2847).

Never unlink physical files. A fresh prepare fingerprint and confirmation are
mandatory; commit rechecks physical absence and all independent references
under BEGIN IMMEDIATE. OriginLink, Artifact and SourceReference are untouched.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from .duplicate_maintenance_guard import candidate_has_active_dependencies_in_db
from .library_declared_links import DeclarationRejected
from .missing_placement_readiness import _physical_absence

_TOKEN = re.compile(r"[0-9a-f]{64}\Z")


def _blocked(reason: str) -> dict[str, object]:
    return {"status": "blocked", "reason": reason}


def _scope(library_root: Path, placement_id: str, identity_id: str):
    if (type(placement_id) is not str or not 0 < len(placement_id) <= 256
            or type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or not isinstance(library_root, Path) or not library_root.is_absolute()):
        return None
    try:
        root = library_root.resolve(strict=True)
        if not root.is_dir():
            return None
        stat = root.stat()
    except (OSError, RuntimeError):
        return None
    return root, stat


def _evidence(db: sqlite3.Connection, *, root: Path, root_stat: os.stat_result,
              placement_id: str, identity_id: str):
    owner = db.execute(
        "SELECT directory_name FROM identities WHERE identity_id=?", (identity_id,)
    ).fetchone()
    if not owner or type(owner[0]) is not str:
        return _blocked("unknown_identity"), None
    directory = owner[0]
    if directory in {"", ".", ".."} or "/" in directory or "\\" in directory:
        return _blocked("invalid_identity_directory"), None
    # A broken/migrated registry may assign the same physical folder to
    # multiple identities. Missing bytes in that folder cannot establish
    # which profile owns the Placement; never issue a retirement token.
    if db.execute(
        "SELECT 1 FROM identities WHERE directory_name=? "
        "AND identity_id<>? LIMIT 1", (directory, identity_id),
    ).fetchone() is not None:
        return _blocked("ambiguous_identity_directory"), None

    row = db.execute(
        "SELECT placement_id,artifact_id,path,identity_id,provider_id,"
        "source_item_id,state,last_seen_at,metadata_json,created_at,updated_at "
        "FROM placements WHERE placement_id=?", (placement_id,)
    ).fetchone()
    if row is None:
        return _blocked("placement_absent"), None
    if row[3] != identity_id:
        return _blocked("identity_mismatch"), None
    if row[6] != "missing":
        return _blocked("not_missing"), None
    path = row[2]
    if type(path) is not str or not path:
        return _blocked("path_out_of_scope"), None
    identity_root = root / directory
    physical = _physical_absence(identity_root, path)
    if physical != "absent":
        return _blocked(physical), None

    # No linked catalogue may be cascaded; no active receipt, backup,
    # checkpoint, declaration or metadata may still name the path.
    # A checkpoint may name only the Placement ID, without repeating its
    # path. Both exact references must be absent before a SQLite retirement.
    # The same fail-closed checker inventories structured and JSON references.
    if (candidate_has_active_dependencies_in_db(
            db, placement_id=placement_id, path=path)
            or candidate_has_active_dependencies_in_db(
                db, placement_id=placement_id, path=placement_id)):
        # Diagnostic only: show *counts* of direct catalogue/backup links.
        # Other checkpoints, declarations, receipts and metadata may still
        # block even if both counts are zero. Never disclose a private path,
        # title, account, provider or identifier, or issue a deletion token.
        # The fail-closed dependency guard above remains authoritative.
        catalogue_rows = db.execute(
            "SELECT COUNT(*) FROM catalog_entries WHERE placement_id=?",
            (placement_id,),
        ).fetchone()[0]
        backup_rows = db.execute(
            "SELECT COUNT(*) FROM backup_items WHERE local_path=?", (path,),
        ).fetchone()[0]
        if (type(catalogue_rows) is not int or catalogue_rows > 128
                or type(backup_rows) is not int or backup_rows > 128):
            return _blocked("dependency_budget"), None
        return {
            **_blocked("dependent_references"),
            "catalogue_rows": catalogue_rows,
            "backup_rows": backup_rows,
        }, None

    envelope = json.dumps(
        [str(root), root_stat.st_dev, root_stat.st_ino, directory, row],
        ensure_ascii=False, separators=(",", ":"), default=str,
    )
    token = hashlib.sha256(envelope.encode("utf-8")).hexdigest()
    return {"status": "ready", "reason": "orphan_missing"}, (
        token, path, identity_root, row[10]
    )


def prepare_missing_placement_retirement(
    storage, *, placement_id: str, identity_id: str, library_root: Path,
) -> dict[str, object]:
    """Read-only, one-Placement preparation. Token is not a delete permit."""
    scope = _scope(library_root, placement_id, identity_id)
    if scope is None:
        return _blocked("invalid_or_unavailable_scope")
    root, stat = scope
    try:
        with storage._lock, storage._connect() as db:
            result, evidence = _evidence(
                db, root=root, root_stat=stat,
                placement_id=placement_id, identity_id=identity_id,
            )
        if evidence is None:
            return result
        return {**result, "plan_token": evidence[0], "requires_confirmation": True}
    except (sqlite3.Error, OSError, ValueError, RuntimeError, DeclarationRejected):
        return _blocked("evidence_unavailable")


def retire_missing_placement(
    storage, *, placement_id: str, identity_id: str, library_root: Path,
    plan_token: str, confirmed: bool = False,
) -> dict[str, object]:
    """Retire only an *orphaned*, physically absent SQLite Placement.

    The caller must present the token of a prior prepare and an explicit
    confirmation. There is no filesystem deletion, cascade, scan or GET.
    A crash rolls back SQLite; a repeated call cannot delete another row.
    """
    if confirmed is not True:
        return _blocked("confirmation_required")
    if type(plan_token) is not str or _TOKEN.fullmatch(plan_token) is None:
        return _blocked("invalid_plan_token")
    scope = _scope(library_root, placement_id, identity_id)
    if scope is None:
        return _blocked("invalid_or_unavailable_scope")
    root, stat = scope

    try:
        with storage._lock:
            db = storage._connect()
            previous_sync = int(db.execute("PRAGMA synchronous").fetchone()[0])
            try:
                # A destructive SQLite-only transaction must be power-loss
                # durable, independently of the ordinary WAL read settings.
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                result, evidence = _evidence(
                    db, root=root, root_stat=stat,
                    placement_id=placement_id, identity_id=identity_id,
                )
                if evidence is None:
                    db.rollback()
                    return result
                token, path, identity_root, updated_at = evidence
                if token != plan_token:
                    db.rollback()
                    return _blocked("stale_plan")
                # The dependency scan can take time. Inspect the leaf again
                # immediately before the DELETE. Never touch its bytes.
                physical = _physical_absence(identity_root, path)
                if physical != "absent":
                    db.rollback()
                    return _blocked(physical)
                cur = db.execute(
                    "DELETE FROM placements WHERE placement_id=? AND identity_id=? "
                    "AND state='missing' AND path=? AND updated_at=?",
                    (placement_id, identity_id, path, updated_at),
                )
                if cur.rowcount != 1:
                    db.rollback()
                    return _blocked("stale_plan")
                db.commit()
                return {"status": "retired", "reason": "orphan_missing"}
            except Exception:
                db.rollback()
                raise
            finally:
                # Must run *after* commit/rollback, never during a transaction.
                db.execute(f"PRAGMA synchronous={previous_sync}")
    except (sqlite3.Error, OSError, ValueError, RuntimeError, DeclarationRejected):
        return _blocked("transaction_failed")
