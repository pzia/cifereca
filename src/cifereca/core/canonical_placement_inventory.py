"""P0 #3185: catalogue-independent inventory of canonical physical Placements.

This is an exhaustive *bounded SQLite snapshot* for one identity. It never
opens files, hashes content, walks directories, infers ownership or authorizes
retirement. Every candidate still needs a fresh Core supervised preview.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Literal

_MAX_PLACEMENTS = 5000
_ALLOWED_STATES = frozenset({
    "present", "missing", "unavailable", "dedup_cleanup_pending",
})


@dataclass(frozen=True, slots=True)
class CanonicalPlacementInventoryRow:
    placement_id: str
    state: str
    catalogued: bool
    legacy_source_bound: bool


@dataclass(frozen=True, slots=True)
class CanonicalPlacementInventoryGroup:
    artifact_id: str
    placements: tuple[CanonicalPlacementInventoryRow, ...]
    status: Literal["candidate_review", "unique", "blocked"]
    reason: str

    @property
    def present_count(self) -> int:
        return sum(row.state == "present" for row in self.placements)


@dataclass(frozen=True, slots=True)
class CanonicalPlacementInventory:
    status: Literal["reviewed", "partial", "over_budget", "unavailable"]
    groups: tuple[CanonicalPlacementInventoryGroup, ...] = ()
    placement_count: int = 0

    @property
    def candidate_count(self) -> int:
        return sum(row.status == "candidate_review" for row in self.groups)

    @property
    def blocked_count(self) -> int:
        return sum(row.status == "blocked" for row in self.groups)


def _valid_id(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 256
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))


def read_canonical_placement_inventory(
    storage, *, identity_id: str,
) -> CanonicalPlacementInventory:
    """Enumerate every Placement of ONE profile, even without catalog_entries.

    'candidate_review' means ONLY that >=2 currently registered Placements
    refer to one consistent Artifact within this profile. No bytes are checked
    here: a fresh supervisor must verify paths, SHA, inodes, dependencies,
    borrower scope and consent under the acquisition lock before any mutation.

    Any SQL overflow or unreadable schema returns no partial "clean" subset.
    Group/row IDs and bounded state only are returned; no paths or credentials.
    """
    if not _valid_id(identity_id):
        return CanonicalPlacementInventory("unavailable")
    try:
        if storage.schema_version() < 14:
            return CanonicalPlacementInventory("unavailable")
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            # The inventory is bounded by profile. Do not materialize every
            # unrelated identity to prove directory isolation: this check
            # belongs to the SAME SQLite snapshot as the Placement query.
            owner = db.execute(
                "SELECT directory_name FROM identities WHERE identity_id=?",
                (identity_id,),
            ).fetchone()
            if owner is None or type(owner[0]) is not str or not owner[0]:
                return CanonicalPlacementInventory("unavailable")
            if db.execute(
                "SELECT 1 FROM identities "
                "WHERE directory_name=? AND identity_id<>? LIMIT 1",
                (owner[0], identity_id),
            ).fetchone() is not None:
                # Shared physical roots forbid cross-profile dedup previews.
                return CanonicalPlacementInventory("unavailable")
            rows = db.execute(
                "SELECT p.placement_id,p.artifact_id,p.state,"
                "p.provider_id,p.source_item_id,a.sha256,a.size,"
                "(SELECT count(*) FROM catalog_entries c WHERE c.placement_id=p.placement_id),"
                "(SELECT count(*) FROM catalog_entries c WHERE c.placement_id=p.placement_id "
                "AND c.artifact_id=p.artifact_id AND c.identity_id=p.identity_id) "
                "FROM placements p LEFT JOIN artifacts a ON a.artifact_id=p.artifact_id "
                "WHERE p.identity_id=? ORDER BY p.artifact_id,p.placement_id LIMIT ?",
                (identity_id, _MAX_PLACEMENTS + 1),
            ).fetchall()
            if len(rows) > _MAX_PLACEMENTS:
                return CanonicalPlacementInventory("over_budget")
            grouped = {}
            for pid, artifact, state, provider, edition, sha, size, ncat, nmatched in rows:
                # Orphan Placements cannot be grouped into a fictitious SHA pair.
                group_key = artifact if _valid_id(artifact) else ("orphan", pid)
                grouped.setdefault(group_key, []).append(
                    (pid, artifact, state, provider, edition, sha,
                     size, ncat, nmatched)
                )
            groups = []
            for records in grouped.values():
                artifact = records[0][1]
                well_formed_artifact = (
                    type(artifact) is str
                    and len(artifact) == 71 and artifact.startswith("sha256:")
                    and all(ch in "0123456789abcdef" for ch in artifact[7:])
                )
                entries = []
                malformed = not well_formed_artifact
                legacy = False
                catalogue_mismatch = False
                present = 0
                for pid, art, state, provider, edition, sha, size, ncat, nmatched in records:
                    valid_artifact = (well_formed_artifact and art == artifact
                                      and sha == artifact[7:]
                                      and type(size) is int and size > 0)
                    if (not _valid_id(pid) or not valid_artifact
                            or state not in _ALLOWED_STATES):
                        malformed = True
                    if state == "present":
                        present += 1
                    bound = provider is not None or edition is not None
                    legacy = legacy or bound
                    catalogue_mismatch = catalogue_mismatch or ncat != nmatched
                    entries.append(CanonicalPlacementInventoryRow(
                        pid if _valid_id(pid) else "",
                        state if state in _ALLOWED_STATES else "unknown",
                        bool(ncat and ncat == nmatched),
                        bound,
                    ))
                if malformed:
                    status, reason = "blocked", "placement_or_artifact_unverified"
                elif catalogue_mismatch:
                    status, reason = "blocked", "catalogue_scope_mismatch"
                elif present != len(entries):
                    status, reason = "blocked", "placement_recovery_required"
                elif legacy:
                    status, reason = "blocked", "legacy_source_scope_unbound"
                elif present >= 2:
                    status, reason = "candidate_review", "physical_and_reference_review_required"
                else:
                    status, reason = "unique", "single_registered_placement"
                groups.append(CanonicalPlacementInventoryGroup(
                    artifact if well_formed_artifact else "",
                    tuple(entries), status, reason,
                ))
            return CanonicalPlacementInventory(
                "partial" if any(g.status == "blocked" for g in groups)
                else "reviewed",
                tuple(groups), len(rows),
            )
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError):
        return CanonicalPlacementInventory("unavailable")


__all__ = [
    "CanonicalPlacementInventoryRow", "CanonicalPlacementInventoryGroup",
    "CanonicalPlacementInventory", "read_canonical_placement_inventory",
]
