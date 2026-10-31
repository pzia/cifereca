"""#3719/#3185: DB-first retirement of an entirely unreferenced canonical copy.

This is a low-level Core primitive, NOT an automatic deduplication policy.
The caller must already have completed all safe reference relinking while
holding the same acquisition lock. If any reference survives, fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import sqlite3
from pathlib import Path
import stat

from .canonical_publication import _same_file
from .canonical_recovery import _lock_ok
from .convergence_verified_presence import _verified_copy
from .duplicate_maintenance_guard import (
    candidate_has_active_dependencies,
    candidate_has_active_dependencies_in_db,
)


@dataclass(frozen=True, slots=True)
class CanonicalRetirementResult:
    retired_placement: bool
    removed_file: bool
    cleanup_pending: bool


def retire_verified_canonical_duplicate(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, shared_lock,
) -> CanonicalRetirementResult:
    """Retire one fully-relocated, same-profile SHA copy; no rights transfer.

    The transaction checks ALL active dependencies before deleting a
    Placement; a failed transaction never unlinks the file. If a crash or
    physical race happens after SQL commit, an extra unindexed file may remain
    for later maintenance, but an active Placement never points at deleted
    bytes. No same-SHA reference is treated as acquisition authorization.
    """
    if (not isinstance(library_root, Path) or not _lock_ok(shared_lock)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not 0 < len(candidate_placement_id) <= 256
            or not 0 < len(keeper_placement_id) <= 256
            or candidate_placement_id == keeper_placement_id):
        raise ValueError("canonical_retire_invalid_request")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        sql = (
            "SELECT path,identity_id,artifact_id,state,provider_id,source_item_id "
            "FROM placements WHERE placement_id=?"
        )
        candidate = db.execute(sql, (candidate_placement_id,)).fetchone()
        keeper = db.execute(sql, (keeper_placement_id,)).fetchone()
        if (candidate is None or keeper is None
                or candidate[0] == keeper[0]
                or candidate[1] != keeper[1] or candidate[2] != keeper[2]
                or candidate[3] != "present" or keeper[3] != "present"
                or candidate[4:] != (None, None)
                or keeper[4:] != (None, None)
                or type(candidate[1]) is not str or not candidate[1]
                or type(candidate[2]) is not str):
            raise ValueError("canonical_retire_scope_changed")
        identity_id, artifact_id = candidate[1:3]
        artifact = db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if (artifact is None or type(artifact[0]) is not str
                or len(artifact[0]) != 64
                or any(ch not in "0123456789abcdef" for ch in artifact[0])
                or artifact_id != "sha256:" + artifact[0]
                or type(artifact[1]) is not int or artifact[1] <= 0):
            raise ValueError("canonical_retire_artifact_invalid")
        directories = db.execute(
            "SELECT identity_id,directory_name FROM identities"
        ).fetchall()
        owners = [name for identity, name in directories if identity == identity_id]
        if (len(owners) != 1 or type(owners[0]) is not str or not owners[0]
                or sum(name == owners[0] for _, name in directories) != 1):
            raise ValueError("canonical_retire_profile_ambiguous")
        reserved = frozenset(
            name for _, name in directories if type(name) is str and name
        )
        observations = []
        for raw_path in (candidate[0], keeper[0]):
            if type(raw_path) is not str or not raw_path:
                raise ValueError("canonical_retire_bytes_changed")
            path = Path(raw_path)
            try:
                before = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise ValueError("canonical_retire_bytes_changed") from exc
            if (not stat.S_ISREG(before.st_mode)
                    or not _verified_copy(
                        raw_path, root=library_root,
                        owner_directory=owners[0],
                        registered_directories=reserved,
                        size=artifact[1], sha256=artifact[0],
                    ) or not _same_file(path, before)):
                raise ValueError("canonical_retire_bytes_changed")
            observations.append((path, before))

        # Catalogues, backup paths, completed/staged recovery receipts,
        # operational metadata, variants and declarations all veto removal
        # until the caller has independently relocated them.
        if candidate_has_active_dependencies_in_db(
            db, placement_id=candidate_placement_id, path=candidate[0],
        ):
            raise ValueError("canonical_retire_active_references")
        if any(not _same_file(path, before) for path, before in observations):
            raise ValueError("canonical_retire_bytes_changed")
        count = db.execute(
            "DELETE FROM placements WHERE placement_id=? AND path=? "
            "AND artifact_id=? AND identity_id=? AND state='present' "
            "AND provider_id IS NULL AND source_item_id IS NULL",
            (candidate_placement_id, candidate[0], artifact_id, identity_id),
        ).rowcount
        if count != 1:
            raise ValueError("canonical_retire_scope_changed")
        if any(not _same_file(path, before) for path, before in observations):
            raise ValueError("canonical_retire_bytes_changed")

    # SQL committed. A failure after this point must never undo the valid
    # keeper or erase a path now reattached by a different writer.
    copy_path, copy_stat = observations[0]
    keeper_path, keeper_stat = observations[1]
    removed = False
    try:
        keeper_now = storage.placement_get(keeper_placement_id)
        if (storage.placement_for_path(copy_path) is None
                and not candidate_has_active_dependencies(
                    storage, placement_id=candidate_placement_id,
                    path=str(copy_path),
                )
                and keeper_now is not None
                and keeper_now.get("state") == "present"
                and keeper_now.get("path") == str(keeper_path)
                and keeper_now.get("identity_id") == identity_id
                and keeper_now.get("artifact_id") == artifact_id
                and keeper_now.get("provider_id") is None
                and keeper_now.get("source_item_id") is None
                and _same_file(copy_path, copy_stat)
                and _same_file(keeper_path, keeper_stat)):
            copy_path.unlink()
            removed = True
    except (OSError, sqlite3.Error):
        # SQL already committed; a post-commit inspection error is advisory.
        pass
    return CanonicalRetirementResult(
        retired_placement=True,
        removed_file=removed,
        cleanup_pending=not removed,
    )


__all__ = [
    "CanonicalRetirementResult",
    "retire_verified_canonical_duplicate",
]
