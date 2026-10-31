"""#3719/#3185: DB-first retirement of an entirely unreferenced canonical copy.

This is a low-level Core primitive, NOT an automatic deduplication policy.
The caller must already have completed all safe reference relinking while
holding the same acquisition lock. If any reference survives, fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
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


_RETIRE_KEY = "core.canonical-dedup-retirement.v1"
_PENDING = "dedup_cleanup_pending"


def _unique_json_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("canonical_retire_duplicate_metadata_field")
        value[key] = item
    return value


def _file_signature(st):
    return {key: int(getattr(st, "st_" + key)) for key in (
        "dev", "ino", "size", "mtime_ns", "ctime_ns",
    )}


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
        # Leave a durable, unindexed tombstone BEFORE any unlink. An
        # interruption must retain both the original pathname and the
        # validated inode fingerprint for an explicit supervised recovery.
        saved = db.execute(
            "SELECT metadata_json FROM placements WHERE placement_id=?",
            (candidate_placement_id,),
        ).fetchone()
        try:
            metadata = json.loads(saved[0], object_pairs_hook=_unique_json_keys)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("canonical_retire_metadata_unreadable") from exc
        if type(metadata) is not dict or _RETIRE_KEY in metadata:
            raise ValueError("canonical_retire_metadata_conflict")
        metadata[_RETIRE_KEY] = {
            "schema": _RETIRE_KEY,
            "keeper_placement_id": keeper_placement_id,
            "stat": _file_signature(observations[0][1]),
        }
        now = int(time.time())
        changed = db.execute(
            "UPDATE placements SET state=?,metadata_json=?,updated_at=? "
            "WHERE placement_id=? AND path=? AND artifact_id=? AND "
            "identity_id=? AND state='present' AND provider_id IS NULL "
            "AND source_item_id IS NULL",
            (_PENDING, json.dumps(metadata, sort_keys=True, separators=(",", ":")),
             now, candidate_placement_id, candidate[0], artifact_id, identity_id),
        )
        if changed.rowcount != 1:
            raise ValueError("canonical_retire_scope_changed")
        if any(not _same_file(path, before) for path, before in observations):
            raise ValueError("canonical_retire_bytes_changed")

    # SQL committed: this persistent state is restart-safe. Cleanup may
    # return 'pending' on any filesystem or second-SQL failure; never lose
    # the original pathname by deleting this Placement row.
    return recover_canonical_duplicate_retirement(
        storage, library_root=library_root,
        candidate_placement_id=candidate_placement_id,
        keeper_placement_id=keeper_placement_id, shared_lock=shared_lock,
    )


def recover_canonical_duplicate_retirement(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, shared_lock,
) -> CanonicalRetirementResult:
    """Resume ONLY a verified, same-profile tombstone under the common lock.

    A recovered file must be the EXACT inode/stat observed before commit, not
    merely an arbitrary equal-SHA replacement at the same path. Unknown
    references, missing keeper or foreign identity leave the original intact.
    The pending Placement remains durable until cleanup completes. After a
    verified unlink, its unique path is released for future acquisitions.
    """
    if (not isinstance(library_root, Path) or not _lock_ok(shared_lock)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not candidate_placement_id or not keeper_placement_id
            or candidate_placement_id == keeper_placement_id):
        raise ValueError("canonical_recover_invalid_request")
    removed = False
    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            columns = (
                "SELECT path,identity_id,artifact_id,state,provider_id,"
                "source_item_id,metadata_json FROM placements WHERE placement_id=?"
            )
            candidate = db.execute(columns, (candidate_placement_id,)).fetchone()
            keeper = db.execute(columns, (keeper_placement_id,)).fetchone()
            if candidate is None:
                # Already finalized, or never staged: never infer ownership.
                return CanonicalRetirementResult(False, False, False)
            if (keeper is None or candidate[3] != _PENDING
                    or keeper[3] != "present"
                    or candidate[0] == keeper[0]
                    or candidate[1:3] != keeper[1:3]
                    or candidate[4:6] != (None, None)
                    or keeper[4:6] != (None, None)
                    or type(candidate[1]) is not str or not candidate[1]
                    or type(candidate[2]) is not str):
                return CanonicalRetirementResult(True, False, True)
            try:
                meta = json.loads(
                    candidate[6], object_pairs_hook=_unique_json_keys,
                )
            except (ValueError, TypeError, RecursionError):
                return CanonicalRetirementResult(True, False, True)
            marker = meta.get(_RETIRE_KEY) if type(meta) is dict else None
            if (type(marker) is not dict or set(marker) != {
                    "schema", "keeper_placement_id", "stat",
                } or marker["schema"] != _RETIRE_KEY
                    or marker["keeper_placement_id"] != keeper_placement_id
                    or type(marker["stat"]) is not dict
                    or set(marker["stat"]) != {
                        "dev", "ino", "size", "mtime_ns", "ctime_ns",
                    } or any(type(v) is not int or v < 0
                             for v in marker["stat"].values())):
                return CanonicalRetirementResult(True, False, True)
            artifact = db.execute(
                "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
                (candidate[2],),
            ).fetchone()
            if (artifact is None or type(artifact[0]) is not str
                    or len(artifact[0]) != 64
                    or any(ch not in "0123456789abcdef" for ch in artifact[0])
                    or candidate[2] != "sha256:" + artifact[0]
                    or type(artifact[1]) is not int or artifact[1] <= 0
                    or marker["stat"]["size"] != artifact[1]):
                return CanonicalRetirementResult(True, False, True)
            identities = db.execute(
                "SELECT identity_id,directory_name FROM identities"
            ).fetchall()
            folders = [folder for profile, folder in identities
                       if profile == candidate[1]]
            if (len(folders) != 1 or type(folders[0]) is not str
                    or not folders[0]
                    or sum(folder == folders[0] for _, folder in identities) != 1):
                return CanonicalRetirementResult(True, False, True)
            registered = frozenset(
                folder for _, folder in identities if type(folder) is str and folder
            )
            keeper_path = Path(keeper[0])
            keeper_stat = os.stat(keeper_path, follow_symlinks=False)
            if (not stat.S_ISREG(keeper_stat.st_mode)
                    or not _verified_copy(
                        keeper[0], root=library_root,
                        owner_directory=folders[0],
                        registered_directories=registered,
                        size=artifact[1], sha256=artifact[0],
                    ) or not _same_file(keeper_path, keeper_stat)):
                return CanonicalRetirementResult(True, False, True)
            # A tombstone itself never authorizes deletion of a newly created
            # reference, including a resumed acquisition staged after a crash.
            if candidate_has_active_dependencies_in_db(
                db, placement_id=candidate_placement_id, path=candidate[0],
            ):
                return CanonicalRetirementResult(True, False, True)
            copy_path = Path(candidate[0])
            try:
                current = os.stat(copy_path, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            if current is not None:
                if (not stat.S_ISREG(current.st_mode)
                        or _file_signature(current) != marker["stat"]
                        or not _verified_copy(
                            candidate[0], root=library_root,
                            owner_directory=folders[0],
                            registered_directories=registered,
                            size=artifact[1], sha256=artifact[0],
                        ) or not _same_file(copy_path, current)
                        or not _same_file(keeper_path, keeper_stat)):
                    return CanonicalRetirementResult(True, False, True)
                # All dependent SQL references were checked under this write
                # transaction, and the two physical files re-hashed above.
                # Crash after this unlink leaves _PENDING + original evidence.
                copy_path.unlink()
                removed = True
            if (os.path.lexists(copy_path)
                    or not _same_file(keeper_path, keeper_stat)):
                return CanonicalRetirementResult(True, removed, True)
            changed = db.execute(
                "DELETE FROM placements WHERE placement_id=? AND path=? "
                "AND identity_id=? AND artifact_id=? AND state=?",
                (candidate_placement_id, candidate[0], candidate[1],
                 candidate[2], _PENDING),
            )
            if changed.rowcount != 1:
                return CanonicalRetirementResult(True, removed, True)
        return CanonicalRetirementResult(True, removed, False)
    except (OSError, sqlite3.Error, ValueError, TypeError, RuntimeError):
        # The SQLite tombstone already exists; an exception after unlink
        # leaves _PENDING for another explicit recovery, not a lost path.
        return CanonicalRetirementResult(True, removed, True)


__all__ = [
    "CanonicalRetirementResult",
    "retire_verified_canonical_duplicate",
    "recover_canonical_duplicate_retirement",
]
