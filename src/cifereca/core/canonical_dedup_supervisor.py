"""#3185/#3719: explicitly supervised canonical duplicate maintenance engine.

Preview is purely read-only. Destructive commit and post-crash recovery require
a separately held common acquisition lock AND confirmed=True. Never call from
ordinary source Sync: a successful B must not trigger a destructive decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .canonical_dedup_preview import preview_canonical_duplicate
from .canonical_dedup_prepare import prepare_verified_canonical_duplicate
from .canonical_dedup_retirement import (
    CanonicalRetirementResult,
    recover_canonical_duplicate_retirement,
    retire_verified_canonical_duplicate,
)
from .canonical_recovery import _lock_ok
from .convergence_verified_presence import _verified_copy


Action = Literal["preview", "commit", "recover"]


@dataclass(frozen=True, slots=True)
class SupervisedCanonicalDedup:
    action: Action
    status: Literal[
        "preview_ready", "preview_blocked", "blocked", "retired",
        "cleanup_pending", "postcheck_failed",
    ]
    reason: str
    catalogue_relinked: int = 0
    backup_relinked: int = 0
    receipt_relinked: int = 0
    removed_file: bool = False

    @property
    def finished(self) -> bool:
        return self.status == "retired"


def _verified_postcheck(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str,
) -> bool:
    """Never report success unless keeper is still B, same-profile and SHAed."""
    try:
        with storage._lock, storage._connect() as db:
            if db.execute(
                "SELECT 1 FROM placements WHERE placement_id=?",
                (candidate_placement_id,),
            ).fetchone():
                return False
            keeper = db.execute(
                "SELECT path,identity_id,artifact_id,state,provider_id,"
                "source_item_id FROM placements WHERE placement_id=?",
                (keeper_placement_id,),
            ).fetchone()
            if (keeper is None or keeper[3] != "present"
                    or keeper[4:] != (None, None)
                    or type(keeper[0]) is not str
                    or type(keeper[1]) is not str or not keeper[1]
                    or type(keeper[2]) is not str):
                return False
            artifact = db.execute(
                "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
                (keeper[2],),
            ).fetchone()
            if (artifact is None or type(artifact[0]) is not str
                    or len(artifact[0]) != 64
                    or keeper[2] != "sha256:" + artifact[0]
                    or type(artifact[1]) is not int or artifact[1] <= 0):
                return False
            profiles = db.execute(
                "SELECT identity_id,directory_name FROM identities"
            ).fetchall()
            owner_dirs = [folder for ident, folder in profiles
                          if ident == keeper[1]]
            if (len(owner_dirs) != 1 or type(owner_dirs[0]) is not str
                    or not owner_dirs[0]
                    or sum(folder == owner_dirs[0]
                           for _, folder in profiles) != 1):
                return False
            allowed = frozenset(
                folder for _, folder in profiles
                if type(folder) is str and folder
            )
        return _verified_copy(
            keeper[0], root=library_root, owner_directory=owner_dirs[0],
            registered_directories=allowed, size=artifact[1],
            sha256=artifact[0],
        )
    except Exception:
        # An unreadable SQLite/FS postcheck must never be a success signal.
        return False


def supervise_canonical_duplicate(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, action: Action = "preview",
    shared_lock=None, confirmed: bool = False,
) -> SupervisedCanonicalDedup:
    """Run the exact Core preview→prepare→validate→commit→postcheck pipeline.

    A failed/partial safe reference migration can be resumed. An uncertain
    deletion is ALWAYS a tombstone requiring explicit 'recover'. No progress
    here authorizes a new GET, modifies a supplier right, or grants adoption.
    """
    if (action not in ("preview", "commit", "recover")
            or not isinstance(library_root, Path)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not 0 < len(candidate_placement_id) <= 256
            or not 0 < len(keeper_placement_id) <= 256
            or candidate_placement_id == keeper_placement_id
            or storage.schema_version() < 14):
        raise ValueError("canonical_supervisor_invalid_request")

    def report(status, reason, *, prep=None, removed=False):
        return SupervisedCanonicalDedup(
            action, status, reason,
            prep.catalogue_relinked if prep else 0,
            prep.backup_relinked if prep else 0,
            prep.receipt_relinked if prep else 0,
            removed,
        )

    def finalize(outcome: CanonicalRetirementResult, *, prep=None):
        if not outcome.retired_placement:
            return report("blocked", "no_retirement_record", prep=prep)
        if outcome.cleanup_pending:
            return report("cleanup_pending", "explicit_recovery_required",
                          prep=prep, removed=outcome.removed_file)
        if not _verified_postcheck(
            storage, library_root=library_root,
            candidate_placement_id=candidate_placement_id,
            keeper_placement_id=keeper_placement_id,
        ):
            return report("postcheck_failed", "keeper_verification_failed",
                          prep=prep, removed=outcome.removed_file)
        return report("retired", "verified_postcheck_complete",
                      prep=prep, removed=outcome.removed_file)

    if action == "preview":
        evidence = preview_canonical_duplicate(
            storage, library_root=library_root,
            candidate_placement_id=candidate_placement_id,
            keeper_placement_id=keeper_placement_id,
        )
        return report(
            "preview_ready" if evidence.status == "unreferenced"
            else "preview_blocked", evidence.reason,
        )

    if confirmed is not True or not _lock_ok(shared_lock):
        raise ValueError("canonical_supervisor_confirmation_and_lock_required")
    kwargs = {
        "library_root": library_root,
        "candidate_placement_id": candidate_placement_id,
        "keeper_placement_id": keeper_placement_id,
        "shared_lock": shared_lock,
    }

    if action == "recover":
        with storage._lock, storage._connect() as db:
            row = db.execute(
                "SELECT state FROM placements WHERE placement_id=?",
                (candidate_placement_id,),
            ).fetchone()
        if row is None or row[0] != "dedup_cleanup_pending":
            return report("blocked", "no_pending_cleanup")
        try:
            return finalize(recover_canonical_duplicate_retirement(storage, **kwargs))
        except ValueError:
            return report("blocked", "recovery_refused")

    evidence = preview_canonical_duplicate(
        storage, library_root=library_root,
        candidate_placement_id=candidate_placement_id,
        keeper_placement_id=keeper_placement_id,
    )
    if evidence.reason not in (
        "verified_no_active_references", "active_references",
    ):
        return report("blocked", "pair_unverified")

    try:
        prepared = prepare_verified_canonical_duplicate(storage, **kwargs)
    except ValueError:
        # Earlier *safe*, independently committed relinks may remain. A
        # retry will skip these idempotently; neither B nor bytes were lost.
        return report("blocked", "relink_refused")
    if prepared.remaining_dependencies:
        return report("blocked", "remaining_dependencies", prep=prepared)

    verified = preview_canonical_duplicate(
        storage, library_root=library_root,
        candidate_placement_id=candidate_placement_id,
        keeper_placement_id=keeper_placement_id,
    )
    if verified.status != "unreferenced":
        return report("blocked", "revalidation_refused", prep=prepared)

    try:
        return finalize(
            retire_verified_canonical_duplicate(storage, **kwargs),
            prep=prepared,
        )
    except ValueError:
        return report("blocked", "commit_refused", prep=prepared)


__all__ = ["SupervisedCanonicalDedup", "supervise_canonical_duplicate"]
