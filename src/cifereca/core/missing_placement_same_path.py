"""P0 #2847: explicit, SQLite-only recovery of a physically returned orphan.

An old state='missing' is not proof that its path is still absent. Reattest
the *exact stored filename* under the pinned identity root and an immutable
Artifact SHA, but do NOT use this narrow primitive for linked catalogues,
attested provider links, backup receipts, variants or opaque operational references.
Those require their own separate source/provenance reconciliation contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path

from .acquisition_audit import _file_proof
from .canonical_recovery import _lock_ok
from .duplicate_maintenance_guard import candidate_has_active_dependencies_in_db
from .library_declared_links import DeclarationRejected
from .missing_placement_retirement import _directory_signature, _scope

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_ROWS = 100_000
_MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024


def _blocked(reason: str) -> dict[str, object]:
    return {"status": "blocked", "reason": reason}


def _unique_pairs(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise ValueError("ambiguous_placement_metadata")
        record[key] = value
    return record


def _evidence(db, *, root: Path, identity_id: str, placement_id: str):
    owner = db.execute(
        "SELECT directory_name FROM identities WHERE identity_id=?",
        (identity_id,),
    ).fetchone()
    if owner is None or type(owner[0]) is not str:
        return _blocked("unknown_identity"), None
    directory = owner[0]
    if (directory in {"", ".", ".."} or "/" in directory or "\\" in directory
            or db.execute(
                "SELECT 1 FROM identities WHERE directory_name=? "
                "AND identity_id<>? LIMIT 1", (directory, identity_id),
            ).fetchone() is not None):
        return _blocked("ambiguous_identity_directory"), None
    row = db.execute(
        "SELECT placement_id,artifact_id,path,identity_id,provider_id,"
        "source_item_id,state,last_seen_at,metadata_json,created_at,updated_at "
        "FROM placements WHERE placement_id=?", (placement_id,),
    ).fetchone()
    if row is None:
        return _blocked("placement_absent"), None
    if row[3] != identity_id:
        return _blocked("identity_mismatch"), None
    if row[6] != "missing":
        return _blocked("not_missing"), None
    path, artifact_id = row[2], row[1]
    if (type(path) is not str or not path or not Path(path).is_absolute()
            or ".." in Path(path).parts or "\x00" in path):
        return _blocked("path_out_of_scope"), None
    if (type(artifact_id) is not str or not artifact_id.startswith("sha256:")
            or _SHA.fullmatch(artifact_id[7:]) is None):
        return _blocked("artifact_unverified"), None
    profile = root / directory
    try:
        rel = Path(path).relative_to(profile)
        if not rel.parts or Path(os.path.abspath(path)) != Path(path):
            return _blocked("path_out_of_scope"), None
    except ValueError:
        return _blocked("path_out_of_scope"), None
    signature = _directory_signature(profile)
    if signature is None:
        return _blocked("identity_directory_unavailable"), None

    # This operator is deliberately restricted to true orphan Placements.
    # A linked catalogue or any path/ID reference needs the richer repair
    # migration, not an implicit status flip based only on matching bytes.
    if db.execute(
        "SELECT 1 FROM catalog_entries WHERE placement_id=? LIMIT 1",
        (placement_id,),
    ).fetchone() is not None:
        return _blocked("linked_catalogue_requires_review"), None
    if (candidate_has_active_dependencies_in_db(
            db, placement_id=placement_id, path=path)
            or candidate_has_active_dependencies_in_db(
                db, placement_id=placement_id, path=placement_id)):
        return _blocked("dependent_references"), None

    metadata = row[8]
    if type(metadata) is not str or not 0 < len(metadata) <= 65536:
        return _blocked("placement_metadata_unavailable"), None
    parsed = json.loads(metadata, object_pairs_hook=_unique_pairs)
    # Only an empty/nonbinding metadata envelope is admissible. A legacy
    # provider_id/source_item_id column is an opaque label, not attestation;
    # but arbitrary JSON keys may carry independent provenance, ownership or
    # operational references not covered by the generic path/ID dependency
    # scanner. Never flip those rows to present by SHA alone.
    if (type(parsed) is not dict or set(parsed) - {"relative_path"}
            or parsed.get("relative_path") not in (None, "")):
        return _blocked("placement_metadata_unavailable"), None

    artifact = db.execute(
        "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if (artifact is None or artifact[0] != artifact_id[7:]
            or type(artifact[1]) is not int
            or not 0 < artifact[1] <= _MAX_FILE_BYTES):
        return _blocked("artifact_unverified"), None
    try:
        before = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return _blocked("file_still_missing"), None
    except OSError:
        return _blocked("file_unavailable"), None
    if not stat.S_ISREG(before.st_mode) or before.st_size != artifact[1]:
        return _blocked("file_not_matching"), None
    if _file_proof(
        root=root, directory=directory, path=path, artifact_id=artifact_id,
    ) != "verified_sha256":
        return _blocked("file_not_verified"), None
    after = os.stat(path, follow_symlinks=False)
    stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if (not stat.S_ISREG(after.st_mode)
            or any(getattr(before, field) != getattr(after, field)
                   for field in stable)
            or signature != _directory_signature(profile)):
        return _blocked("file_changed"), None
    root_stat = os.stat(root, follow_symlinks=False)
    fingerprint = [
        identity_id, placement_id, str(root),
        (root_stat.st_dev, root_stat.st_ino),
        directory, signature, list(row), list(artifact),
        [getattr(after, field) for field in stable],
    ]
    token = hashlib.sha256(json.dumps(
        fingerprint, ensure_ascii=False, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()
    return {"status": "ready", "reason": "orphan_reappeared"}, (
        token, path, row[10], metadata, signature,
        root_stat.st_dev, root_stat.st_ino,
    )


def preview_reappeared_missing_placement(
    storage, *, placement_id: str, identity_id: str, library_root: Path,
) -> dict[str, object]:
    """Read-only preview. A token alone never authorizes a state change."""
    scope = _scope(library_root, placement_id, identity_id)
    if scope is None:
        return _blocked("invalid_or_unavailable_scope")
    root, _root_stat = scope
    try:
        with storage._lock, storage._connect() as db:
            db.execute("SAVEPOINT reappeared_placement_preview")
            try:
                result, evidence = _evidence(
                    db, root=root, identity_id=identity_id,
                    placement_id=placement_id,
                )
            finally:
                db.execute("RELEASE SAVEPOINT reappeared_placement_preview")
        if evidence is None:
            return result
        return {**result, "plan_token": evidence[0],
                "requires_confirmation": True}
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError,
            RecursionError, DeclarationRejected):
        return _blocked("evidence_unavailable")


def restore_reappeared_missing_placement(
    storage, *, placement_id: str, identity_id: str, library_root: Path,
    plan_token: str, confirmed: bool, shared_lock,
) -> dict[str, object]:
    """CAS reactivation, NEVER relink a source, catalogue, receipt or file."""
    if confirmed is not True:
        return _blocked("confirmation_required")
    if type(plan_token) is not str or _SHA.fullmatch(plan_token) is None:
        return _blocked("invalid_plan_token")
    if not _lock_ok(shared_lock):
        return _blocked("acquisition_lock_required")
    scope = _scope(library_root, placement_id, identity_id)
    if scope is None:
        return _blocked("invalid_or_unavailable_scope")
    root, scope_stat = scope
    try:
        with storage._lock:
            db = storage._connect()
            previous_sync = int(db.execute("PRAGMA synchronous").fetchone()[0])
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                result, evidence = _evidence(
                    db, root=root, identity_id=identity_id,
                    placement_id=placement_id,
                )
                if evidence is None:
                    db.rollback()
                    return result
                token, path, updated, metadata, signature, dev, ino = evidence
                if token != plan_token:
                    db.rollback()
                    return _blocked("stale_plan")
                updated_row = db.execute(
                    "UPDATE placements SET state='present',updated_at="
                    "MAX(updated_at+1,CAST(strftime('%s','now') AS INTEGER)) "
                    "WHERE placement_id=? AND identity_id=? AND path=? "
                    "AND state='missing' AND updated_at=? AND metadata_json=?",
                    (placement_id, identity_id, path, updated, metadata),
                )
                if updated_row.rowcount != 1:
                    db.rollback()
                    return _blocked("stale_plan")
                # A nofollow verifier must run again under the same writer
                # lock after the CAS, immediately before the durable commit.
                current_root = os.stat(root, follow_symlinks=False)
                if ((current_root.st_dev, current_root.st_ino) != (dev, ino)
                        or (scope_stat.st_dev, scope_stat.st_ino) != (dev, ino)
                        or _directory_signature(
                            root / db.execute(
                                "SELECT directory_name FROM identities "
                                "WHERE identity_id=?", (identity_id,),
                            ).fetchone()[0]
                        ) != signature
                        or _file_proof(
                            root=root,
                            directory=db.execute(
                                "SELECT directory_name FROM identities "
                                "WHERE identity_id=?", (identity_id,),
                            ).fetchone()[0],
                            path=path,
                            artifact_id=db.execute(
                                "SELECT artifact_id FROM placements "
                                "WHERE placement_id=?", (placement_id,),
                            ).fetchone()[0],
                        ) != "verified_sha256"):
                    db.rollback()
                    return _blocked("file_changed")
                db.commit()
                return {"status": "restored", "reason": "orphan_reappeared"}
            except Exception:
                db.rollback()
                raise
            finally:
                db.execute(f"PRAGMA synchronous={previous_sync}")
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError,
            RecursionError, DeclarationRejected):
        return _blocked("transaction_failed")


__all__ = [
    "preview_reappeared_missing_placement",
    "restore_reappeared_missing_placement",
]
