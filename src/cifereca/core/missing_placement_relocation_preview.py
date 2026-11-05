"""#2847: bounded, read-only evidence for a *manually relocated* local file.

An equal SHA is proof of identical bytes, not of an entitlement. This probe
never rebinds a Placement, mutates a catalogue, imports a file, calls providers,
or authorizes a later write. Revalidation is mandatory for a separate Core
transaction holding the acquisition lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from .canonical_publication import _same_file
from .convergence_verified_presence import _verified_copy
from .duplicate_maintenance_guard import candidate_has_active_dependencies_in_db
from .library_declared_links import DeclarationRejected
from .missing_placement_readiness import _physical_absence
from .missing_placement_retirement import _directory_signature


def _blocked(reason: str) -> dict[str, object]:
    return {"status": "blocked", "reason": reason}


def _admissible_source_coords(provider: object, item: object) -> bool:
    """Accept a complete provider scope or a local-only SQL NULL/NULL pair.

    A half-known or empty source must never be upgraded to local ownership.
    """
    return (
        (provider is None and item is None)
        or (type(provider) is str and bool(provider)
            and type(item) is str and bool(item))
    )


def preview_missing_placement_relocation(
    storage, *, library_root: Path, identity_id: str, placement_id: str,
    candidate_path: Path,
) -> dict[str, object]:
    """Propose one attested physical target without granting ownership.

    All returned values are synthetic labels/counts or an opaque digest; no
    raw local path, account or title is exported by this private diagnostic.
    A review_required result is NOT a permit to mutate. No physical movement.
    """
    if (not isinstance(library_root, Path) or not library_root.is_absolute()
            or not isinstance(candidate_path, Path)
            or not candidate_path.is_absolute()
            # A lexical "alice/../bob" alias must never count as bytes
            # under alice even when a downstream re-hash succeeds.
            or ".." in candidate_path.parts
            or type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or type(placement_id) is not str or not 0 < len(placement_id) <= 256):
        return _blocked("invalid_scope")
    try:
        root = library_root.resolve(strict=True)
        if not root.is_dir():
            return _blocked("root_unavailable")
        root_stat = root.stat()
    except (OSError, ValueError, RuntimeError):
        return _blocked("root_unavailable")

    try:
        with storage._lock, storage._connect() as db:
            # One SQLite snapshot for identities, old placement, Artifact,
            # target conflicts and dependency inventory. No write transaction.
            db.execute("BEGIN")
            identities = db.execute(
                "SELECT identity_id,directory_name FROM identities LIMIT 100002"
            ).fetchall()
            if len(identities) > 100001:
                return _blocked("identity_budget")
            folders = [folder for owner, folder in identities
                       if owner == identity_id]
            if len(folders) != 1 or type(folders[0]) is not str:
                return _blocked("unknown_identity")
            directory = folders[0]
            if (directory in {"", ".", ".."} or "/" in directory
                    or "\\" in directory
                    or sum(name == directory for _, name in identities) != 1):
                return _blocked("ambiguous_identity_directory")

            record = db.execute(
                "SELECT path,identity_id,artifact_id,state,provider_id,"
                "source_item_id,metadata_json,updated_at FROM placements "
                "WHERE placement_id=?", (placement_id,),
            ).fetchone()
            if record is None:
                return _blocked("placement_absent")
            old_path, actual_owner, artifact_id, state, provider, item, metadata, updated = record
            if actual_owner != identity_id:
                return _blocked("identity_mismatch")
            if state != "missing":
                return _blocked("not_missing")
            if (type(old_path) is not str or not old_path
                    or not _admissible_source_coords(provider, item)):
                return _blocked("source_coordinates_unavailable")
            profile_signature = _directory_signature(root / directory)
            if (profile_signature is None
                    or _physical_absence(root / directory, old_path) != "absent"):
                return _blocked("old_path_not_verified_absent")

            artifact = db.execute(
                "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if (artifact is None or type(artifact_id) is not str
                    or type(artifact[0]) is not str or len(artifact[0]) != 64
                    or any(ch not in "0123456789abcdef" for ch in artifact[0])
                    or artifact_id != "sha256:" + artifact[0]
                    or type(artifact[1]) is not int or artifact[1] <= 0):
                return _blocked("artifact_evidence_unavailable")

            candidate = str(candidate_path)
            if candidate == old_path:
                return _blocked("same_path")
            # No competing Placement may already own the target, regardless
            # of identity or Artifact SHA. Duplicate merge is a separate act.
            if db.execute(
                "SELECT 1 FROM placements WHERE path=? LIMIT 1", (candidate,)
            ).fetchone():
                return _blocked("target_placement_occupied")
            # A legacy projection can claim a target without a Placement.
            # Never attach such a target to a different account by equal SHA.
            if db.execute(
                "SELECT 1 FROM backup_items WHERE local_path=? LIMIT 1",
                (candidate,),
            ).fetchone():
                return _blocked("target_backup_occupied")
            if candidate_has_active_dependencies_in_db(
                db, placement_id="__unbound_target__", path=candidate
            ):
                return _blocked("target_referenced")

            # Hash actual bytes under the registered, unique profile folder.
            # Existing Core validator excludes symlinks in each ancestor and
            # attests inode + size + digest across the hash operation.
            directories = frozenset(
                name for _, name in identities if type(name) is str and name
            )
            if not _verified_copy(
                candidate, root=root, owner_directory=directory,
                registered_directories=directories,
                size=artifact[1], sha256=artifact[0],
            ):
                return _blocked("candidate_bytes_unverified")
            before = os.stat(candidate, follow_symlinks=False)
            if (not _same_file(candidate_path, before)
                    or _directory_signature(root / directory) != profile_signature):
                return _blocked("candidate_changed")

            linked = db.execute(
                "SELECT COUNT(*) FROM catalog_entries WHERE placement_id=?",
                (placement_id,),
            ).fetchone()[0]
            backup = db.execute(
                "SELECT COUNT(*) FROM backup_items WHERE local_path=?",
                (old_path,),
            ).fetchone()[0]
            needs_relink = candidate_has_active_dependencies_in_db(
                db, placement_id=placement_id, path=old_path
            ) or candidate_has_active_dependencies_in_db(
                db, placement_id=placement_id, path=placement_id
            )
            token_payload = [
                identity_id, placement_id, str(root),
                int(root_stat.st_dev), int(root_stat.st_ino),
                directory, profile_signature, old_path, artifact_id, updated, metadata,
                provider, item, candidate, artifact,
                [int(getattr(before, "st_" + field))
                 for field in ("dev", "ino", "size", "mtime_ns", "ctime_ns")],
                linked, backup, needs_relink,
            ]
            digest = hashlib.sha256(json.dumps(
                token_payload, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            return {
                "status": "review_required",
                "reason": ("references_require_migration"
                           if needs_relink else "exact_bytes_candidate"),
                "candidate_bytes_verified": True,
                "catalogue_rows": int(linked),
                "backup_rows": int(backup),
                "requires_confirmation": True,
                "plan_token": digest,
            }
    except (sqlite3.Error, OSError, ValueError, RuntimeError, DeclarationRejected):
        return _blocked("evidence_unavailable")
