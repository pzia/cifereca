"""#2847: detach exact missing local catalogue projections, then retire Placement.

Only SQLite row mutations. No file deletion, network/provider access, Artifact,
OriginLink, SourceReference, entitlement, receipt or backup mutation. This is
an operator-only *candidate* until the private inspection, consent and shared
acquisition lock are attested. Unknown dependencies always roll back.
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
from .duplicate_maintenance_guard import (
    _mentions_exact_path, candidate_has_active_dependencies_in_db,
)
from .library_declared_links import DeclarationRejected
from .missing_placement_readiness import _physical_absence
from .missing_placement_retirement import _blocked, _scope, _directory_signature

_TOKEN = re.compile(r"[0-9a-f]{64}\Z")
_SENTINEL = "__2847_linked_retirement_probe__"


def _unique_object(pairs):
    """No duplicate key may silently hide a path or scope identifier."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous_catalogue_json")
        result[key] = value
    return result


def _evidence(db, *, root: Path, root_stat: os.stat_result,
              placement_id: str, identity_id: str):
    owner = db.execute(
        "SELECT directory_name FROM identities WHERE identity_id=?",
        (identity_id,),
    ).fetchone()
    if owner is None or type(owner[0]) is not str:
        return _blocked("unknown_identity"), None
    directory = owner[0]
    if (directory in ("", ".", "..") or "/" in directory or "\\" in directory
            or db.execute(
                "SELECT COUNT(*) FROM identities WHERE directory_name=?",
                (directory,),
            ).fetchone()[0] != 1):
        return _blocked("ambiguous_identity_directory"), None

    row = db.execute(
        "SELECT placement_id,artifact_id,path,identity_id,provider_id,"
        "source_item_id,state,last_seen_at,metadata_json,created_at,updated_at "
        "FROM placements WHERE placement_id=?", (placement_id,),
    ).fetchone()
    if row is None:
        return _blocked("placement_absent"), None
    pid, artifact, old_path, owner_id, provider, item, state, _, raw_meta, _, _ = row
    if owner_id != identity_id:
        return _blocked("identity_mismatch"), None
    if state != "missing":
        return _blocked("not_missing"), None
    if (type(artifact) is not str or not artifact
            or type(provider) is not str or not provider
            or type(item) is not str or not item
            or type(old_path) is not str or not old_path):
        return _blocked("source_coordinates_unavailable"), None
    profile_root = root / directory
    profile_signature = _directory_signature(profile_root)
    if (profile_signature is None
            or _physical_absence(profile_root, old_path) != "absent"):
        return _blocked("old_path_not_verified_absent"), None
    # The detached catalogue must retain a real, already known Artifact.
    # Never treat a dangling Artifact ID as adequate historical evidence.
    if db.execute(
        "SELECT 1 FROM artifacts WHERE artifact_id=? LIMIT 1", (artifact,),
    ).fetchone() is None:
        return _blocked("artifact_evidence_unavailable"), None
    # A Placement with unclassified metadata may carry historical evidence
    # independently of its linked catalogue. Don't discard that evidence.
    if type(raw_meta) is not str or len(raw_meta) > 65536:
        return _blocked("placement_metadata_requires_review"), None
    stored_meta = json.loads(raw_meta, object_pairs_hook=_unique_object)
    if type(stored_meta) is not dict or stored_meta:
        return _blocked("placement_metadata_requires_review"), None

    linked = db.execute(
        "SELECT catalog_entry_id,artifact_id,placement_id,identity_id,"
        "provider_id,item_id,local_state,metadata_json,updated_at "
        "FROM catalog_entries WHERE placement_id=? "
        "ORDER BY catalog_entry_id LIMIT 129",
        (pid,),
    ).fetchall()
    if not linked:
        return _blocked("no_linked_catalogue"), None
    if len(linked) > 128:
        return _blocked("catalogue_budget"), None
    if db.execute(
        "SELECT 1 FROM backup_items WHERE local_path=? LIMIT 1",
        (old_path,),
    ).fetchone():
        return _blocked("backup_requires_separate_relink"), None

    # A projection is removable ONLY as a local path assertion, never as a
    # provider edition, acquisition, provenance or right assertion.
    # Preserve its original title/author/provider/item/Artifact and all
    # metadata fields other than the one direct stale physical path.
    changed = []
    for cid, art, linked_pid, own, prov, edition, local_state, raw, updated in linked:
        if (art != artifact or linked_pid != pid or own != identity_id
                or prov != provider or edition != item or local_state != "missing"
                or type(raw) is not str or len(raw) > 65536):
            return _blocked("catalogue_scope_conflict"), None
        document = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(document) is not dict
                or document.get("path") not in (None, "", old_path)
                or document.get("relative_path") not in (None, "")
                or any(_mentions_exact_path(value, old_path)
                       for key, value in document.items() if key != "path")
                or any(_mentions_exact_path(value, pid)
                       for value in document.values())):
            return _blocked("catalogue_metadata_requires_review"), None
        if document.get("path") not in (None, ""):
            del document["path"]
        # Keep the source catalogue and Artifact identity, clear only its
        # physical location and current local-presence assertion.
        changed.append((cid, raw, updated, json.dumps(document, ensure_ascii=False)))

    envelope = json.dumps(
        [str(root), int(root_stat.st_dev), int(root_stat.st_ino),
         identity_id, directory, profile_signature, row, linked],
        ensure_ascii=False, separators=(",", ":"), default=str,
    )
    token = hashlib.sha256(envelope.encode("utf-8")).hexdigest()
    return {
        "status": "review_required",
        "reason": "catalogue_detachment_requires_confirmation",
        "catalogue_rows": len(changed),
    }, (token, old_path, directory, profile_signature, row, changed)


def prepare_missing_catalogue_detachment(
    storage, *, placement_id: str, identity_id: str, library_root: Path,
) -> dict[str, object]:
    """Candidate only: no SQL/FS write, no source ownership inferred."""
    scoped = _scope(library_root, placement_id, identity_id)
    if scoped is None:
        return _blocked("invalid_or_unavailable_scope")
    root, stat = scoped
    try:
        with storage._lock:
            db = storage._connect()
            db.execute("BEGIN")
            try:
                result, evidence = _evidence(
                    db, root=root, root_stat=stat,
                    placement_id=placement_id, identity_id=identity_id,
                )
            finally:
                db.rollback()
        if evidence is None:
            return result
        return {
            **result,
            "plan_token": evidence[0],
            "requires_confirmation": True,
        }
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError,
            DeclarationRejected, json.JSONDecodeError):
        return _blocked("evidence_unavailable")


def retire_missing_linked_catalogue(
    storage, *, placement_id: str, identity_id: str, library_root: Path,
    plan_token: str, confirmed: bool, shared_lock,
) -> dict[str, object]:
    """One confirmed SQLite transaction; fail closed on ANY remaining reference.

    A prior private completed inspection must supply the selected case and a
    separately recorded human confirmation. No file is deleted or moved.
    """
    if confirmed is not True:
        return _blocked("confirmation_required")
    if not _lock_ok(shared_lock):
        return _blocked("acquisition_lock_required")
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
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                result, evidence = _evidence(
                    db, root=root, root_stat=stat,
                    placement_id=placement_id, identity_id=identity_id,
                )
                if evidence is None:
                    db.rollback()
                    return result
                token, old_path, directory, profile_signature, row, changed = evidence
                if token != plan_token:
                    db.rollback()
                    return _blocked("stale_plan")
                cur = db.execute(
                    "DELETE FROM placements WHERE placement_id=? AND "
                    "identity_id=? AND state='missing' AND path=? AND "
                    "updated_at=? AND metadata_json=?",
                    (placement_id, identity_id, old_path, row[10], row[8]),
                )
                if cur.rowcount != 1:
                    db.rollback()
                    return _blocked("stale_plan")
                now = int(time.time())
                for cid, raw, updated, cleaned in changed:
                    cur = db.execute(
                        "UPDATE catalog_entries SET placement_id=NULL,"
                        "local_state='missing',metadata_json=?,updated_at=? "
                        "WHERE catalog_entry_id=? AND placement_id=? AND "
                        "local_state='missing' AND metadata_json=? AND "
                        "updated_at=?",
                        (cleaned, now, cid, placement_id, raw, updated),
                    )
                    if cur.rowcount != 1:
                        db.rollback()
                        return _blocked("stale_plan")
                # Test the *resulting* graph, while still inside the write
                # transaction. Any external backup, declaration, ledger,
                # checkpoint, JSON metadata or dangling catalogue path/ID
                # forces rollback of ALL updates and the DELETE.
                if db.execute(
                    "SELECT 1 FROM placements WHERE placement_id=?",
                    (_SENTINEL,),
                ).fetchone():
                    db.rollback()
                    return _blocked("dependency_evidence_unavailable")
                for reference in (old_path, placement_id):
                    if candidate_has_active_dependencies_in_db(
                        db, placement_id=_SENTINEL, path=reference,
                    ):
                        db.rollback()
                        return _blocked("unmigrated_reference")
                current = root.stat()
                if ((int(current.st_dev), int(current.st_ino))
                        != (int(stat.st_dev), int(stat.st_ino))
                        or _directory_signature(root / directory) != profile_signature
                        or _physical_absence(root / directory, old_path) != "absent"):
                    db.rollback()
                    return _blocked("filesystem_changed")
                db.commit()
                return {"status": "retired", "reason": "catalogue_detached",
                        "catalogue_rows": len(changed)}
            except Exception:
                db.rollback()
                raise
            finally:
                db.execute(f"PRAGMA synchronous={previous_sync}")
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError,
            DeclarationRejected, json.JSONDecodeError):
        return _blocked("transaction_failed")
