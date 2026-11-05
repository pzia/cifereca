"""#2847: explicit, SQLite-only relink of one verified missing Placement.

No filesystem rename/unlink, network request or right/provenance mutation.
Linked catalogue rows are migrated only when their exact source coordinates
are unambiguous. Unknown references and backup/receipt paths fail closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from .canonical_recovery import _lock_ok
from .convergence_verified_presence import _verified_copy
from .duplicate_maintenance_guard import (
    _mentions_exact_path, candidate_has_active_dependencies_in_db,
)
from .library_declared_links import DeclarationRejected
from .missing_placement_readiness import _physical_absence
from .missing_placement_retirement import _directory_signature
from .missing_placement_relocation_preview import (
    _blocked, preview_missing_placement_relocation,
)

_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _unique_json_fields(pairs):
    """Reject ambiguous metadata before changing any linked catalogue."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_relocation_metadata_field")
        result[key] = value
    return result


def _validated_evidence(db, *, root: Path, identity_id: str,
                        placement_id: str, candidate_path: Path):
    """Recreate the preview digest against the writer's locked SQLite state."""
    roots = db.execute(
        "SELECT identity_id,directory_name FROM identities LIMIT 100002",
    ).fetchall()
    if len(roots) > 100001:
        return _blocked("identity_budget"), None
    folders = [name for owner, name in roots if owner == identity_id]
    if len(folders) != 1 or type(folders[0]) is not str:
        return _blocked("unknown_identity"), None
    directory = folders[0]
    if (directory in {"", ".", ".."} or "/" in directory or "\\" in directory
            or sum(name == directory for _, name in roots) != 1):
        return _blocked("ambiguous_identity_directory"), None

    row = db.execute(
        "SELECT path,identity_id,artifact_id,state,provider_id,"
        "source_item_id,metadata_json,updated_at FROM placements "
        "WHERE placement_id=?", (placement_id,),
    ).fetchone()
    if row is None:
        return _blocked("placement_absent"), None
    old_path, owner, artifact_id, state, provider, item, metadata, updated = row
    if owner != identity_id:
        return _blocked("identity_mismatch"), None
    if state != "missing":
        return _blocked("not_missing"), None
    if (type(old_path) is not str or not old_path
            or type(provider) is not str or not provider
            or type(item) is not str or not item):
        return _blocked("source_coordinates_unavailable"), None
    profile_signature = _directory_signature(root / directory)
    if (profile_signature is None
            or _physical_absence(root / directory, old_path) != "absent"):
        return _blocked("old_path_not_verified_absent"), None
    artifact = db.execute(
        "SELECT sha256,size FROM artifacts WHERE artifact_id=?", (artifact_id,),
    ).fetchone()
    if (artifact is None or type(artifact_id) is not str
            or type(artifact[0]) is not str or _SHA.fullmatch(artifact[0]) is None
            or artifact_id != "sha256:" + artifact[0]
            or type(artifact[1]) is not int or artifact[1] <= 0):
        return _blocked("artifact_evidence_unavailable"), None

    candidate = str(candidate_path)
    if candidate == old_path:
        return _blocked("same_path"), None
    if db.execute(
        "SELECT 1 FROM placements WHERE path=? LIMIT 1", (candidate,),
    ).fetchone():
        return _blocked("target_placement_occupied"), None
    if db.execute(
        "SELECT 1 FROM backup_items WHERE local_path=? LIMIT 1", (candidate,),
    ).fetchone():
        return _blocked("target_backup_occupied"), None
    # Preserve the preview's exact target-ownership veto, including opaque
    # metadata namespaces and declarations.
    if candidate_has_active_dependencies_in_db(
        db, placement_id="__unbound_target__", path=candidate,
    ):
        return _blocked("target_referenced"), None
    directories = frozenset(
        name for _, name in roots if type(name) is str and name
    )
    if not _verified_copy(
        candidate, root=root, owner_directory=directory,
        registered_directories=directories, size=artifact[1], sha256=artifact[0],
    ):
        return _blocked("candidate_bytes_unverified"), None
    before = os.stat(candidate, follow_symlinks=False)
    from .canonical_publication import _same_file
    if (not _same_file(candidate_path, before)
            or _directory_signature(root / directory) != profile_signature):
        return _blocked("candidate_changed"), None

    linked = db.execute(
        "SELECT COUNT(*) FROM catalog_entries WHERE placement_id=?",
        (placement_id,),
    ).fetchone()[0]
    backup = db.execute(
        "SELECT COUNT(*) FROM backup_items WHERE local_path=?", (old_path,),
    ).fetchone()[0]
    needs_relink = (
        candidate_has_active_dependencies_in_db(
            db, placement_id=placement_id, path=old_path,
        )
        or candidate_has_active_dependencies_in_db(
            db, placement_id=placement_id, path=placement_id,
        )
    )
    root_stat = root.stat()
    envelope = [
        identity_id, placement_id, str(root), int(root_stat.st_dev),
        int(root_stat.st_ino), directory, profile_signature,
        old_path, artifact_id, updated,
        metadata, provider, item, candidate, artifact,
        [int(getattr(before, "st_" + field))
         for field in ("dev", "ino", "size", "mtime_ns", "ctime_ns")],
        linked, backup, needs_relink,
    ]
    token = hashlib.sha256(json.dumps(
        envelope, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {"status": "verified", "token": token}, (
        old_path, artifact_id, provider, item, metadata, updated, artifact,
        directory, directories, int(root_stat.st_dev), int(root_stat.st_ino),
        profile_signature,
    )


def relocate_missing_placement(
    storage, *, library_root: Path, identity_id: str, placement_id: str,
    candidate_path: Path, plan_token: str, confirmed: bool, shared_lock,
) -> dict[str, object]:
    """Commit a prepared, operator-confirmed A→B path fix, never a new right.

    Only a currently missing path and an independently hash-verified target
    owned by the SAME profile are admissible. The caller already holds the
    normal acquisition interprocess lock; this function owns BEGIN IMMEDIATE.
    Repeated requests fail closed after the first successful commit.
    """
    if confirmed is not True:
        return _blocked("confirmation_required")
    if (not _lock_ok(shared_lock) or type(plan_token) is not str
            or _SHA.fullmatch(plan_token) is None
            or not isinstance(library_root, Path)
            or not library_root.is_absolute()
            or not isinstance(candidate_path, Path)
            or not candidate_path.is_absolute() or ".." in candidate_path.parts
            or type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or type(placement_id) is not str or not 0 < len(placement_id) <= 256):
        return _blocked("invalid_request")
    # Validate against the public read-only preparer *before* taking a write
    # transaction, so a forged plan token is never a write permission.
    preflight = preview_missing_placement_relocation(
        storage, library_root=library_root, identity_id=identity_id,
        placement_id=placement_id, candidate_path=candidate_path,
    )
    if preflight.get("status") != "review_required":
        return _blocked(str(preflight.get("reason", "evidence_unavailable")))
    if preflight.get("plan_token") != plan_token:
        return _blocked("stale_plan")
    try:
        root = library_root.resolve(strict=True)
        if not root.is_dir():
            return _blocked("root_unavailable")
        with storage._lock:
            db = storage._connect()
            previous_sync = int(db.execute("PRAGMA synchronous").fetchone()[0])
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                checked, evidence = _validated_evidence(
                    db, root=root, identity_id=identity_id,
                    placement_id=placement_id, candidate_path=candidate_path,
                )
                if evidence is None:
                    db.rollback()
                    return checked
                if checked["token"] != plan_token:
                    db.rollback()
                    return _blocked("stale_plan")
                (old_path, artifact_id, provider, item, metadata, updated,
                 artifact, directory, directories, dev, ino,
                 profile_signature) = evidence

                # A legacy backup's path requires separate source/receipt
                # attestation (#3185). Never silently migrate its account.
                if db.execute(
                    "SELECT 1 FROM backup_items WHERE local_path=? LIMIT 1",
                    (old_path,),
                ).fetchone():
                    db.rollback()
                    return _blocked("backup_requires_separate_relink")
                if type(metadata) is not str or len(metadata) > 65536:
                    db.rollback()
                    return _blocked("placement_metadata_unavailable")
                meta = json.loads(metadata, object_pairs_hook=_unique_json_fields)
                if (type(meta) is not dict
                        or meta.get("relative_path") not in (None, "")):
                    db.rollback()
                    return _blocked("placement_metadata_requires_relink")
                # Only a directly linked catalogue with the exact provider,
                # edition, owner and Artifact may be retargeted. No inferred
                # ownership from title or SHA. Nested path mentions block.
                rows = db.execute(
                    "SELECT catalog_entry_id,identity_id,provider_id,item_id,"
                    "artifact_id,local_state,metadata_json "
                    "FROM catalog_entries WHERE placement_id=? LIMIT 129",
                    (placement_id,),
                ).fetchall()
                if len(rows) > 128:
                    db.rollback()
                    return _blocked("catalogue_budget")
                updates = []
                for cid, own, prov, edition, art, state, raw in rows:
                    if (own != identity_id or prov != provider or edition != item
                            or art != artifact_id or state != "missing"
                            or type(raw) is not str or len(raw) > 65536):
                        db.rollback()
                        return _blocked("catalogue_scope_conflict")
                    document = json.loads(raw, object_pairs_hook=_unique_json_fields)
                    if (type(document) is not dict
                            or document.get("path") not in (None, "", old_path)
                            or document.get("relative_path") not in (None, "")
                            or any(_mentions_exact_path(val, old_path)
                                   for key, val in document.items()
                                   if key != "path")):
                        db.rollback()
                        return _blocked("catalogue_metadata_requires_relink")
                    document["path"] = str(candidate_path)
                    updates.append((cid, json.dumps(document, ensure_ascii=False)))

                now = int(time.time())
                cur = db.execute(
                    "UPDATE placements SET path=?,state='present',updated_at=? "
                    "WHERE placement_id=? AND path=? AND identity_id=? "
                    "AND artifact_id=? AND provider_id=? AND source_item_id=? "
                    "AND state='missing' AND updated_at=? AND metadata_json=?",
                    (str(candidate_path), now, placement_id, old_path,
                     identity_id, artifact_id, provider, item, updated, metadata),
                )
                if cur.rowcount != 1:
                    db.rollback()
                    return _blocked("stale_plan")
                for cid, document in updates:
                    db.execute(
                        "UPDATE catalog_entries SET local_state='local',"
                        "metadata_json=?,updated_at=? WHERE catalog_entry_id=? "
                        "AND placement_id=?",
                        (document, now, cid, placement_id),
                    )
                # After migrating only attested direct projections, any
                # remaining path reference or ID-only operational checkpoint
                # blocks the whole transaction. Never change the right graph.
                sentinel = "__2847_unbound_probe__"
                if db.execute(
                    "SELECT 1 FROM placements WHERE placement_id=?", (sentinel,)
                ).fetchone():
                    db.rollback()
                    return _blocked("dependency_evidence_unavailable")
                for old_reference in (old_path, placement_id):
                    if candidate_has_active_dependencies_in_db(
                        db, placement_id=sentinel, path=old_reference,
                    ):
                        db.rollback()
                        return _blocked("unmigrated_reference")
                # A mount replacement or a concurrent filesystem edit cannot
                # transform a stale plan into a live local placement.
                current_root = root.stat()
                if (int(current_root.st_dev), int(current_root.st_ino)) != (dev, ino):
                    db.rollback()
                    return _blocked("root_changed")
                if (_directory_signature(root / directory) != profile_signature
                        or _physical_absence(root / directory, old_path) != "absent"
                        or not _verified_copy(
                            str(candidate_path), root=root,
                            owner_directory=directory,
                            registered_directories=directories,
                            size=artifact[1], sha256=artifact[0],
                        )):
                    db.rollback()
                    return _blocked("filesystem_changed")
                db.commit()
                return {"status": "relocated", "catalogue_rows": len(updates)}
            except Exception:
                db.rollback()
                raise
            finally:
                db.execute(f"PRAGMA synchronous={previous_sync}")
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError,
            DeclarationRejected, json.JSONDecodeError):
        return _blocked("transaction_failed")
