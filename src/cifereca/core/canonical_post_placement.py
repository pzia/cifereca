"""#3719: read-only post-publication physical duplicate candidates.

The first successful Core B Placement and OriginLink are authoritative.
Matching SHA alone neither authorizes adoption nor deletion of another path.
"""
from __future__ import annotations

from pathlib import Path
import os
import hashlib
import json
import sqlite3

from .convergence_verified_presence import _verified_copy

_MAX_MATCHES = 64


def verified_post_placement_duplicates(
    storage, *, library_root: Path, placement_id: str,
) -> tuple[str, ...]:
    """Return only same-profile, rehashed placement IDs; never mutate files/SQL."""
    if (not isinstance(library_root, Path) or type(placement_id) is not str
            or not placement_id or storage.schema_version() < 14):
        return ()
    with storage._lock, storage._connect() as db:
        row = db.execute(
            "SELECT p.identity_id,p.path,p.artifact_id,p.state,a.sha256,a.size "
            "FROM placements AS p JOIN artifacts AS a ON a.artifact_id=p.artifact_id "
            "WHERE p.placement_id=?", (placement_id,),
        ).fetchone()
        if (row is None or row[3] != "present"
                or type(row[0]) is not str or not row[0]
                or type(row[4]) is not str or len(row[4]) != 64
                or type(row[5]) is not int or row[5] <= 0
                or row[2] != "sha256:" + row[4]):
            return ()
        identity, target, artifact_id, _, digest, size = row
        directories = db.execute(
            "SELECT identity_id,directory_name FROM identities"
        ).fetchall()
        owners = [name for owner, name in directories if owner == identity]
        if len(owners) != 1 or type(owners[0]) is not str or not owners[0]:
            return ()
        owner_dir = owners[0]
        # Two profile IDs sharing one directory cannot provide physical
        # profile isolation, even if the placement rows name one owner.
        if sum(name == owner_dir for _, name in directories) != 1:
            return ()
        reserved = frozenset(name for _, name in directories
                             if type(name) is str and name)
        candidates = db.execute(
            "SELECT placement_id,path FROM placements "
            "WHERE identity_id=? AND artifact_id=? AND state='present' "
            "AND placement_id<>? ORDER BY placement_id LIMIT ?",
            (identity, artifact_id, placement_id, _MAX_MATCHES + 1),
        ).fetchall()
        if not candidates or len(candidates) > _MAX_MATCHES:
            # No duplicate => no extra full-file SHA pass on every successful
            # delivery. The publisher has already verified its own bytes.
            return ()
    def valid(path):
        return _verified_copy(
            path, root=library_root, owner_directory=owner_dir,
            registered_directories=reserved, size=size, sha256=digest,
        )
    if not valid(target):
        return ()
    return tuple(other_id for other_id, path in candidates if valid(path))


def preview_post_placement_supervised_pairs(
    storage, *, library_root: Path, placement_id: str,
) -> tuple[tuple[str, str, str], ...]:
    """Bridge post-B candidates into #3185 preview without committing deletion.

    A source Sync MUST NOT invoke the destructive supervisor. The returned
    triplets contain only candidate placement IDs, status and reason: no
    filesystem paths, provider credentials or receipt data.
    """
    from .canonical_dedup_supervisor import supervise_canonical_duplicate

    candidates = verified_post_placement_duplicates(
        storage, library_root=library_root, placement_id=placement_id,
    )
    results = []
    for candidate in candidates:
        try:
            outcome = supervise_canonical_duplicate(
                storage, library_root=library_root,
                candidate_placement_id=candidate,
                keeper_placement_id=placement_id, action="preview",
            )
        except (ValueError, sqlite3.Error, OSError):
            # A stale candidate or unreadable provenance is not permission
            # to deduplicate. Keep other independent previews available.
            results.append((candidate, "preview_blocked", "preview_unavailable"))
            continue
        results.append((candidate, outcome.status, outcome.reason))
    return tuple(results)


_PENDING_PREFIX = "core.canonical-dedup-pending.v1."


def checkpoint_post_placement_duplicates(
    storage, *, placement_id: str, duplicate_placement_ids: tuple[str, ...],
) -> str:
    """Backward-compatible opaque checkpoint key for diagnostics and tools."""
    key, _changed = checkpoint_post_placement_duplicates_change(
        storage, placement_id=placement_id,
        duplicate_placement_ids=duplicate_placement_ids,
    )
    return key


def checkpoint_post_placement_duplicates_change(
    storage, *, placement_id: str, duplicate_placement_ids: tuple[str, ...],
) -> tuple[str, bool]:
    """Persist an advisory, profile-scoped maintenance queue entry.

    The caller must already have re-hashed the paths via
    verified_post_placement_duplicates. The checkpoint contains only opaque
    placement IDs and an Artifact fingerprint, never a source permission,
    historical receipt, content URL or physical path. An executor must still
    check dependencies and verify bytes again before ANY future mutation.
    """
    if (type(placement_id) is not str or not 0 < len(placement_id) <= 256
            or type(duplicate_placement_ids) is not tuple
            or not 0 < len(duplicate_placement_ids) <= _MAX_MATCHES
            or any(type(pid) is not str or not 0 < len(pid) <= 256
                   or pid == placement_id for pid in duplicate_placement_ids)
            or len(set(duplicate_placement_ids)) != len(duplicate_placement_ids)):
        raise ValueError("canonical_dedup_invalid_candidates")

    candidates = tuple(sorted(duplicate_placement_ids))
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT identity_id,artifact_id,state FROM placements "
            "WHERE placement_id=?", (placement_id,),
        ).fetchone()
        if (row is None or row[2] != "present"
                or type(row[0]) is not str or not row[0]
                or type(row[1]) is not str or not row[1].startswith("sha256:")
                or len(row[1]) != 71):
            raise ValueError("canonical_dedup_target_changed")
        identity, artifact_id, _ = row
        matches = db.execute(
            "SELECT placement_id FROM placements WHERE identity_id=? "
            "AND artifact_id=? AND state='present' AND placement_id IN ("
            + ",".join("?" for _ in candidates) + ")",
            (identity, artifact_id, *candidates),
        ).fetchall()
        if {pid for (pid,) in matches} != set(candidates):
            raise ValueError("canonical_dedup_candidate_changed")

        key = _PENDING_PREFIX + hashlib.sha256(json.dumps(
            [identity, artifact_id], separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        payload = json.dumps({
            "schema": "canonical.post-placement-dedup-pending.v1",
            "identity_id": identity,
            "artifact_id": artifact_id,
            "placement_ids": sorted((placement_id, *candidates)),
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        existing = db.execute(
            "SELECT value FROM metadata WHERE key=?", (key,),
        ).fetchone()
        changed = existing is None or existing[0] != payload
        if changed:
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, payload),
            )
    return key, changed




def _unique_checkpoint_fields(pairs):
    """Reject ambiguous advisory JSON rather than selecting the last value."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("canonical_dedup_duplicate_checkpoint_field")
        result[key] = value
    return result


def clear_checkpoint_if_no_duplicates(
    storage, *, library_root: Path, placement_id: str,
) -> bool:
    """Expire ONLY a proven stale advisory entry; never change book ownership.

    A missing/symlinked keeper, ambiguous profile directory, a still-present
    (even corrupted) candidate, or an unknown checkpoint schema leaves the
    diagnostic intact for review. Hashing happens only if a marker exists.
    """
    if (type(placement_id) is not str or not placement_id
            or not isinstance(library_root, Path)):
        return False
    with storage._lock, storage._connect() as db:
        row = db.execute(
            "SELECT p.identity_id,p.path,p.artifact_id,p.state,a.sha256,a.size "
            "FROM placements p JOIN artifacts a ON a.artifact_id=p.artifact_id "
            "WHERE p.placement_id=?", (placement_id,),
        ).fetchone()
        if (row is None or row[3] != "present" or type(row[0]) is not str
                or not row[0] or row[2] != "sha256:" + str(row[4])
                or type(row[5]) is not int or row[5] <= 0):
            return False
        identity, path, artifact, _, digest, size = row
        key = _PENDING_PREFIX + hashlib.sha256(json.dumps(
            [identity, artifact], separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        marked = db.execute(
            "SELECT value FROM metadata WHERE key=?", (key,),
        ).fetchone()
        if marked is None or type(marked[0]) is not str:
            return False
        try:
            record = json.loads(marked[0], object_pairs_hook=_unique_checkpoint_fields)
        except (ValueError, TypeError, RecursionError):
            return False
        if (type(record) is not dict or set(record) != {
                "schema", "identity_id", "artifact_id", "placement_ids",
            } or record["schema"] != "canonical.post-placement-dedup-pending.v1"
                or record["identity_id"] != identity
                or record["artifact_id"] != artifact
                or type(record["placement_ids"]) is not list
                or not 2 <= len(record["placement_ids"]) <= _MAX_MATCHES + 1
                or any(type(pid) is not str or not 0 < len(pid) <= 256
                       for pid in record["placement_ids"])
                or len(set(record["placement_ids"])) != len(record["placement_ids"])
                or placement_id not in record["placement_ids"]):
            return False
        registered = db.execute(
            "SELECT identity_id,directory_name FROM identities"
        ).fetchall()
        owners = [directory for owner, directory in registered if owner == identity]
        if (len(owners) != 1 or type(owners[0]) is not str
                or sum(directory == owners[0] for _, directory in registered) != 1):
            return False
        owner_dir = owners[0]
        reserved = frozenset(directory for _, directory in registered
                             if type(directory) is str and directory)
        former_ids = tuple(
            pid for pid in record["placement_ids"] if pid != placement_id
        )
        former = db.execute(
            "SELECT placement_id,identity_id,artifact_id,state,path "
            "FROM placements WHERE placement_id IN ("
            + ",".join("?" for _ in former_ids) + ")",
            former_ids,
        ).fetchall()
        if len(former) != len(former_ids):
            # A deleted row contains no path: we cannot prove that its bytes
            # were cleaned up merely because the registration disappeared.
            return False
        former_paths = []
        for pid, candidate_owner, candidate_artifact, state, candidate_path in former:
            if (pid not in former_ids or candidate_owner != identity
                    or candidate_artifact != artifact or state == "present"
                    or type(candidate_path) is not str):
                return False
            previous = Path(candidate_path)
            # Missing registered placement is not missing physical bytes.
            # Preserve the queue until EACH previous pathname is absent.
            if (not previous.is_absolute() or ".." in previous.parts
                    or os.path.lexists(previous)):
                return False
            former_paths.append((pid, candidate_path, state))
        if len(former_paths) != len(former_ids):
            return False
    if not _verified_copy(
        path, root=library_root, owner_directory=owner_dir,
        registered_directories=reserved, size=size, sha256=digest,
    ):
        return False
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Never erase a pending marker when a second physical Placement
        # remains, even if its bytes no longer match the stored artifact SHA.
        present = db.execute(
            "SELECT placement_id,path,state FROM placements "
            "WHERE identity_id=? AND artifact_id=? AND state='present'",
            (identity, artifact),
        ).fetchall()
        if present != [(placement_id, path, "present")]:
            return False
        for pid, candidate_path, state in former_paths:
            if (db.execute(
                "SELECT path,state FROM placements WHERE placement_id=? "
                "AND identity_id=? AND artifact_id=?",
                (pid, identity, artifact),
            ).fetchone() != (candidate_path, state)
                    or os.path.lexists(candidate_path)):
                return False
        if db.execute(
            "SELECT 1 FROM placements WHERE placement_id=? "
            "AND identity_id=? AND artifact_id=? AND state='present' "
            "AND path=?",
            (placement_id, identity, artifact, path),
        ).fetchone() is None:
            return False
        result = db.execute(
            "DELETE FROM metadata WHERE key=? AND value=?",
            (key, marked[0]),
        )
        return result.rowcount == 1


__all__ = [
    "verified_post_placement_duplicates",
    "preview_post_placement_supervised_pairs",
    "checkpoint_post_placement_duplicates",
    "clear_checkpoint_if_no_duplicates",
]
