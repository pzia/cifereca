"""#3185: bounded, read-only discovery of durable B-cleanup tombstones.

This is *not* a recovery authorization. It returns only IDs and fixed status
codes; an explicit confirmed Core supervisor call must freshly verify the
keeper's bytes, all dependencies and the stored inode before any unlink.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal

from .canonical_dedup_retirement import _RETIRE_KEY, _PENDING, _unique_json_keys

_LIMIT = 1000
_MAX_JSON_BYTES = 65536


@dataclass(frozen=True, slots=True)
class PendingCanonicalCleanup:
    candidate_placement_id: str
    keeper_placement_id: str
    identity_id: str


@dataclass(frozen=True, slots=True)
class PendingCanonicalCleanupReview:
    status: Literal["reviewed", "partial", "over_budget", "unavailable"]
    candidates: tuple[PendingCanonicalCleanup, ...] = ()
    unverified_count: int = 0

    @property
    def recovery_candidates(self) -> int:
        """Advisory count only, never implicit consent or filesystem proof."""
        return len(self.candidates)


def review_pending_canonical_cleanups(
    storage, *, identity_id: str | None = None,
) -> PendingCanonicalCleanupReview:
    """List *only* scoped, internally consistent durable recovery pair IDs.

    Never mutates DB, runs filesystem cleanup or acquires provider rights.
    A malformed/unknown tombstone is counted as unverified and does not
    produce a recovery candidate. Above the row budget, nothing is exposed.
    """
    if (identity_id is not None and (
            type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in identity_id)
    )):
        return PendingCanonicalCleanupReview("unavailable")
    try:
        if storage.schema_version() < 14:
            return PendingCanonicalCleanupReview("unavailable")
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            args: tuple[str, ...] = (_PENDING,)
            query = (
                "SELECT placement_id,path,identity_id,artifact_id,"
                "provider_id,source_item_id,"
                "CASE WHEN length(metadata_json)>? THEN NULL ELSE metadata_json END "
                "FROM placements WHERE state=?"
            )
            args = (str(_MAX_JSON_BYTES), _PENDING)
            # length() is evaluated inside SQLite; do not transfer a huge
            # opaque metadata payload merely to discover one pending task.
            if identity_id is not None:
                query += " AND identity_id=?"
                args += (identity_id,)
            query += " ORDER BY placement_id LIMIT 1001"
            rows = db.execute(query, args).fetchall()
            if len(rows) > _LIMIT:
                return PendingCanonicalCleanupReview("over_budget")
            profiles = db.execute(
                "SELECT identity_id,directory_name FROM identities"
            ).fetchall()
            allowed: dict[str, str] = {}
            for owner, folder in profiles:
                if type(owner) is str and type(folder) is str and folder:
                    allowed[owner] = folder
            unique_dirs = {
                folder for folder in allowed.values()
                if sum(x == folder for x in allowed.values()) == 1
            }
            findings: list[PendingCanonicalCleanup] = []
            invalid = 0
            for candidate_id, path, owner, artifact_id, provider, item, raw in rows:
                valid = (
                    type(candidate_id) is str and 0 < len(candidate_id) <= 256
                    and type(owner) is str and bool(owner)
                    and type(path) is str and bool(path)
                    and Path(path).is_absolute() and ".." not in Path(path).parts
                    and type(artifact_id) is str
                    and provider is None and item is None
                    and allowed.get(owner) in unique_dirs
                    and type(raw) is str and 0 < len(raw) <= _MAX_JSON_BYTES
                )
                if not valid:
                    invalid += 1
                    continue
                try:
                    evidence = json.loads(raw, object_pairs_hook=_unique_json_keys)
                    marker = evidence[_RETIRE_KEY]
                except (ValueError, TypeError, KeyError, RecursionError):
                    invalid += 1
                    continue
                if (type(evidence) is not dict or type(marker) is not dict
                        or set(marker) != {"schema", "keeper_placement_id", "stat"}
                        or marker["schema"] != _RETIRE_KEY
                        or type(marker["keeper_placement_id"]) is not str
                        or not 0 < len(marker["keeper_placement_id"]) <= 256
                        or marker["keeper_placement_id"] == candidate_id
                        or type(marker["stat"]) is not dict
                        or set(marker["stat"]) != {
                            "dev", "ino", "size", "mtime_ns", "ctime_ns",
                        }
                        or any(type(value) is not int or value < 0
                               for value in marker["stat"].values())):
                    invalid += 1
                    continue
                keeper = db.execute(
                    "SELECT path,identity_id,artifact_id,state,provider_id,"
                    "source_item_id FROM placements WHERE placement_id=?",
                    (marker["keeper_placement_id"],),
                ).fetchone()
                artifact = db.execute(
                    "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()
                if (keeper is None or keeper[1] != owner
                        or keeper[2] != artifact_id
                        or keeper[3:] != ("present", None, None)
                        or type(keeper[0]) is not str or not keeper[0]
                        or keeper[0] == path or not Path(keeper[0]).is_absolute()
                        or ".." in Path(keeper[0]).parts
                        or artifact is None
                        or type(artifact[0]) is not str
                        or len(artifact[0]) != 64
                        or any(c not in "0123456789abcdef" for c in artifact[0])
                        or artifact_id != "sha256:" + artifact[0]
                        or type(artifact[1]) is not int or artifact[1] <= 0
                        or marker["stat"]["size"] != artifact[1]):
                    invalid += 1
                    continue
                findings.append(PendingCanonicalCleanup(
                    candidate_id, marker["keeper_placement_id"], owner,
                ))
            return PendingCanonicalCleanupReview(
                "partial" if invalid else "reviewed", tuple(findings), invalid,
            )
    except Exception:
        # An SQLite/schema/JSON failure is never an empty and proven-clean
        # queue. Report it as unavailable, without source details.
        return PendingCanonicalCleanupReview("unavailable")


__all__ = [
    "PendingCanonicalCleanup", "PendingCanonicalCleanupReview",
    "review_pending_canonical_cleanups",
]
