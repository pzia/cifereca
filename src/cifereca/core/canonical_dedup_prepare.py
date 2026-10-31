"""#3719/#3185: explicit, non-destructive orchestration of B-reference relinks.

The Core acquisition lock must already be held. Every relink independently
commits and verifies same-profile physical bytes under BEGIN IMMEDIATE.
A failure halfway through can leave *references migrated*, never a removed
file; the operation can be resumed idempotently under the same admission.
An eventual retire/cleanup is a SEPARATE explicit operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .canonical_dedup_catalog_relink import relink_canonical_duplicate_catalogue
from .canonical_dedup_backup_relink import relink_canonical_duplicate_backup_paths
from .canonical_dedup_receipt_relink import relink_completed_delivery_checkpoints
from .canonical_post_placement import verified_post_placement_duplicates
from .canonical_recovery import _lock_ok
from .duplicate_maintenance_guard import candidate_has_active_dependencies


@dataclass(frozen=True, slots=True)
class CanonicalDedupPreparation:
    catalogue_relinked: int
    backup_relinked: int
    receipt_relinked: int
    remaining_dependencies: bool

    @property
    def ready_for_separate_retirement_review(self) -> bool:
        """Advisory only. NEVER an authorization to delete a file."""
        return not self.remaining_dependencies


def prepare_verified_canonical_duplicate(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, shared_lock,
) -> CanonicalDedupPreparation:
    """Migrate only audited reference classes, preserving both physical copies.

    A later destructive call MUST recheck profiles, SHA, dependency classes,
    path, inode, and the shared lock in its own transaction. Unrecognized
    references remain an explicit blocker; no blanket rewrite is attempted.
    """
    if (not isinstance(library_root, Path) or not _lock_ok(shared_lock)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not 0 < len(candidate_placement_id) <= 256
            or not 0 < len(keeper_placement_id) <= 256
            or candidate_placement_id == keeper_placement_id
            or storage.schema_version() < 14):
        raise ValueError("canonical_prepare_invalid_request")
    # A complete physical duplicate verification is mandatory *before*
    # migrating a single reference. A SHA of a foreign-profile file, a
    # corrupt local copy, or an unknown placement can never pass.
    matches = verified_post_placement_duplicates(
        storage, library_root=library_root, placement_id=keeper_placement_id,
    )
    if candidate_placement_id not in matches:
        raise ValueError("canonical_prepare_pair_unverified")

    kwargs = {
        "library_root": library_root,
        "candidate_placement_id": candidate_placement_id,
        "keeper_placement_id": keeper_placement_id,
        "shared_lock": shared_lock,
    }
    # Independent transactions intentionally let a retry resume a partially
    # completed *safe* relink. No call below is allowed to unlink/delete B.
    catalogue = relink_canonical_duplicate_catalogue(storage, **kwargs)
    backup = relink_canonical_duplicate_backup_paths(storage, **kwargs)
    receipt = relink_completed_delivery_checkpoints(storage, **kwargs)

    # A concurrent external writer may bypass the shared lock. The result is
    # only a diagnostic, never a stored destructive grant. Rehash again so
    # even this advisory status cannot be green on stale physical evidence.
    if candidate_placement_id not in verified_post_placement_duplicates(
        storage, library_root=library_root, placement_id=keeper_placement_id,
    ):
        raise ValueError("canonical_prepare_pair_changed")
    current = storage.placement_get(candidate_placement_id)
    if (current is None or current.get("state") != "present"
            or type(current.get("path")) is not str):
        raise ValueError("canonical_prepare_pair_changed")
    remaining = candidate_has_active_dependencies(
        storage, placement_id=candidate_placement_id, path=current["path"],
    )
    return CanonicalDedupPreparation(catalogue, backup, receipt, remaining)


__all__ = ["CanonicalDedupPreparation", "prepare_verified_canonical_duplicate"]
