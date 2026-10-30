"""#3719: read-only preflight of a *persisted* post-B dedup checkpoint.

This is a maintenance diagnostic, NOT a permission to delete, relink, borrow,
or skip an authorized GET. An executor must revalidate every fact in its own
write transaction and under the shared acquisition lock before mutation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Literal

from .canonical_post_placement import (
    _MAX_MATCHES, _PENDING_PREFIX, verified_post_placement_duplicates,
)
from .duplicate_maintenance_guard import candidate_has_active_dependencies


CandidateState = Literal[
    "unreferenced", "references_need_migration", "legacy_scope",
    "physical_unverified", "changed",
]


@dataclass(frozen=True, slots=True)
class CandidateReview:
    placement_id: str
    state: CandidateState

    @property
    def unreferenced(self) -> bool:
        """Advisory only; not permission to unlink."""
        return self.state == "unreferenced"


@dataclass(frozen=True, slots=True)
class MaintenanceReview:
    status: Literal["absent", "stale", "reviewed"]
    candidates: tuple[CandidateReview, ...] = ()


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("canonical_dedup_duplicate_checkpoint_key")
        result[key] = value
    return result


def review_pending_canonical_duplicates(
    storage, *, library_root: Path, placement_id: str,
) -> MaintenanceReview:
    """Inspect a durable checkpoint with fresh B and path-dependency evidence.

    No raw paths, credentials, rights, receipts or file contents are returned.
    Any invalid/missing account/placement, malformed checkpoint or oversized
    queue is stale rather than silently classified as safe to retire.
    """
    if (not isinstance(library_root, Path) or type(placement_id) is not str
            or not 0 < len(placement_id) <= 256
            or storage.schema_version() < 14):
        return MaintenanceReview("stale")
    with storage._lock, storage._connect() as db:
        target = db.execute(
            "SELECT identity_id,artifact_id,state,provider_id,source_item_id "
            "FROM placements WHERE placement_id=?", (placement_id,),
        ).fetchone()
        if (target is None or target[2] != "present"
                or type(target[0]) is not str or not target[0]
                or type(target[1]) is not str or not target[1].startswith("sha256:")):
            return MaintenanceReview("stale")
        identity, artifact, _, target_provider, target_edition = target
        key = _PENDING_PREFIX + hashlib.sha256(json.dumps(
            [identity, artifact], separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")).hexdigest()
        record = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if record is None:
            return MaintenanceReview("absent")
        try:
            if type(record[0]) is not str or len(record[0]) > 65536:
                return MaintenanceReview("stale")
            payload = json.loads(record[0], object_pairs_hook=_unique_fields)
        except (ValueError, TypeError, RecursionError):
            return MaintenanceReview("stale")
        if (type(payload) is not dict
                or set(payload) != {"schema", "identity_id", "artifact_id", "placement_ids"}
                or payload["schema"] != "canonical.post-placement-dedup-pending.v1"
                or payload["identity_id"] != identity or payload["artifact_id"] != artifact
                or type(payload["placement_ids"]) is not list
                or not 2 <= len(payload["placement_ids"]) <= _MAX_MATCHES + 1
                or any(type(item) is not str or not 0 < len(item) <= 256
                       for item in payload["placement_ids"])
                or len(set(payload["placement_ids"])) != len(payload["placement_ids"])
                or placement_id not in payload["placement_ids"]):
            return MaintenanceReview("stale")
        others = tuple(item for item in payload["placement_ids"] if item != placement_id)
        current = db.execute(
            "SELECT placement_id,identity_id,artifact_id,state,provider_id,"
            "source_item_id,path FROM placements WHERE placement_id IN ("
            + ",".join("?" for _ in others) + ")", others,
        ).fetchall()
        lookup = {row[0]: row for row in current}
    # The target itself must be a canonical B copy; legacy source/edition
    # coordinates cannot be collapsed on an Artifact SHA alone.
    if target_provider is not None or target_edition is not None:
        return MaintenanceReview("reviewed", tuple(
            CandidateReview(item, "legacy_scope") for item in others
        ))
    # Reread actual bytes in the profile; stored sha/queue are not proof.
    # This check is advisory: a later destructive transaction MUST recheck.
    try:
        freshly_verified = frozenset(verified_post_placement_duplicates(
            storage, library_root=library_root, placement_id=placement_id,
        ))
    except (OSError, ValueError, RuntimeError):
        return MaintenanceReview("stale")
    decisions = []
    for item in others:
        row = lookup.get(item)
        if (row is None or row[1] != identity or row[2] != artifact
                or row[3] != "present"):
            state: CandidateState = "changed"
        elif row[4] is not None or row[5] is not None:
            state = "legacy_scope"
        elif item not in freshly_verified:
            state = "physical_unverified"
        elif candidate_has_active_dependencies(
            storage, placement_id=item, path=row[6],
        ):
            state = "references_need_migration"
        else:
            state = "unreferenced"
        decisions.append(CandidateReview(item, state))
    return MaintenanceReview("reviewed", tuple(decisions))


__all__ = [
    "CandidateReview", "MaintenanceReview", "review_pending_canonical_duplicates",
]
