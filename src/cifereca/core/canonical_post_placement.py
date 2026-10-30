"""#3719: read-only post-publication physical duplicate candidates.

The first successful Core B Placement and OriginLink are authoritative.
Matching SHA alone neither authorizes adoption nor deletion of another path.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json

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


_PENDING_PREFIX = "core.canonical-dedup-pending.v1."


def checkpoint_post_placement_duplicates(
    storage, *, placement_id: str, duplicate_placement_ids: tuple[str, ...],
) -> str:
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
        if existing is None or existing[0] != payload:
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, payload),
            )
    return key


__all__ = [
    "verified_post_placement_duplicates",
    "checkpoint_post_placement_duplicates",
]
