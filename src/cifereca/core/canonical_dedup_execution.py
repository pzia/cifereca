"""#3185/#3719: one explicit Core coordinator for canonical deduplication.

Never called automatically by acquisition or Sync. The same live acquisition
lock covers all stages. Relinks are independent SQLite transactions: a crash
between them is safe because BOTH physical copies remain present. Retirement
is attempted only after the shared dependency guard proves nothing still
references the candidate. Post-commit recovery is delegated to the Core
tombstone protocol; no path or source entitlement is inferred from SHA alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from .canonical_dedup_prepare import prepare_verified_canonical_duplicate
from .canonical_dedup_preview import preview_canonical_duplicate
from .canonical_dedup_retirement import (
    recover_canonical_duplicate_retirement,
    retire_verified_canonical_duplicate,
)
from .canonical_recovery import _lock_ok
from .convergence_verified_presence import _verified_copy


@dataclass(frozen=True, slots=True)
class CanonicalDedupExecutionResult:
    status: str
    reason: str
    catalogue_relinked: int = 0
    backup_relinked: int = 0
    receipt_relinked: int = 0
    retired_placement: bool = False
    removed_file: bool = False
    cleanup_pending: bool = False


def _keeper_verified(
    storage, *, library_root: Path, keeper_placement_id: str,
) -> bool:
    """Postcheck only the surviving same-profile published Artifact bytes."""
    try:
        with storage._lock, storage._connect() as db:
            keeper = db.execute(
                "SELECT p.identity_id,p.artifact_id,p.path,p.state,"
                "p.provider_id,p.source_item_id,a.sha256,a.size "
                "FROM placements p JOIN artifacts a "
                "ON p.artifact_id=a.artifact_id WHERE p.placement_id=?",
                (keeper_placement_id,),
            ).fetchone()
            if (keeper is None or keeper[3:6] != ("present", None, None)
                    or type(keeper[0]) is not str or not keeper[0]
                    or type(keeper[1]) is not str or type(keeper[2]) is not str
                    or type(keeper[6]) is not str or len(keeper[6]) != 64
                    or any(c not in "0123456789abcdef" for c in keeper[6])
                    or keeper[1] != "sha256:" + keeper[6]
                    or type(keeper[7]) is not int or keeper[7] <= 0):
                return False
            owners = db.execute(
                "SELECT identity_id,directory_name FROM identities",
            ).fetchall()
            folders = [folder for identity, folder in owners
                       if identity == keeper[0]]
            if (len(folders) != 1 or type(folders[0]) is not str
                    or not folders[0]
                    or sum(folder == folders[0] for _, folder in owners) != 1):
                return False
            registered = frozenset(
                folder for _, folder in owners if type(folder) is str and folder
            )
        return _verified_copy(
            keeper[2], root=library_root, owner_directory=folders[0],
            registered_directories=registered, size=keeper[7],
            sha256=keeper[6],
        )
    except (OSError, ValueError, TypeError, sqlite3.Error, RuntimeError):
        return False


def execute_canonical_duplicate(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str, shared_lock, retire: bool = False,
) -> CanonicalDedupExecutionResult:
    """Explicit preview → relink → guard → retire → postcheck/recover.

    Caller must own the live common acquisition lock. Use retire=False to
    migrate only attested references and inspect the remaining blockers.
    Setting retire=True is an EXPLICIT destructive Core request, not a Sync
    default. An unknown/changed reference aborts without any physical unlink;
    SQL relinks already committed remain safe and repeatable.
    """
    if (not isinstance(library_root, Path) or not _lock_ok(shared_lock)
            or type(candidate_placement_id) is not str
            or type(keeper_placement_id) is not str
            or not 0 < len(candidate_placement_id) <= 256
            or not 0 < len(keeper_placement_id) <= 256
            or candidate_placement_id == keeper_placement_id
            or type(retire) is not bool):
        raise ValueError("canonical_dedup_execution_invalid_request")

    # A crash after SQL retirement, before unlink, is resumed ONLY from the
    # persisted tombstone + inode evidence. Never re-run catalogue migration
    # or infer a new origin during this cleanup-only path.
    candidate = storage.placement_get(candidate_placement_id)
    if candidate is not None and candidate.get("state") == "dedup_cleanup_pending":
        if not retire:
            return CanonicalDedupExecutionResult(
                "blocked", "cleanup_requires_explicit_recovery",
                cleanup_pending=True,
            )
        outcome = recover_canonical_duplicate_retirement(
            storage, library_root=library_root,
            candidate_placement_id=candidate_placement_id,
            keeper_placement_id=keeper_placement_id,
            shared_lock=shared_lock,
        )
        if not _keeper_verified(
            storage, library_root=library_root,
            keeper_placement_id=keeper_placement_id,
        ):
            return CanonicalDedupExecutionResult(
                "blocked", "keeper_postcheck_failed",
                retired_placement=outcome.retired_placement,
                removed_file=outcome.removed_file, cleanup_pending=True,
            )
        return CanonicalDedupExecutionResult(
            "cleanup_pending" if outcome.cleanup_pending else "completed",
            "cleanup_pending" if outcome.cleanup_pending else "recovered",
            retired_placement=outcome.retired_placement,
            removed_file=outcome.removed_file,
            cleanup_pending=outcome.cleanup_pending,
        )

    first = preview_canonical_duplicate(
        storage, library_root=library_root,
        candidate_placement_id=candidate_placement_id,
        keeper_placement_id=keeper_placement_id,
    )
    if first.status not in ("unreferenced", "blocked") or first.reason not in (
        "verified_no_active_references", "active_references",
    ):
        return CanonicalDedupExecutionResult("blocked", first.reason)

    catalogue = backups = receipts = 0
    try:
        # One canonical non-destructive preparation owns ALL reference
        # migration. Do not duplicate its sequencing or proof policy here.
        prepared = prepare_verified_canonical_duplicate(
            storage, library_root=library_root,
            candidate_placement_id=candidate_placement_id,
            keeper_placement_id=keeper_placement_id,
            shared_lock=shared_lock,
        )
        catalogue = prepared.catalogue_relinked
        backups = prepared.backup_relinked
        receipts = prepared.receipt_relinked
    except (ValueError, sqlite3.Error, OSError):
        # A late refusal leaves both physical copies intact; successful
        # earlier relinks are durable but must be counted via a new audit.
        return CanonicalDedupExecutionResult(
            "blocked", "reference_relink_failed",
        )

    final = preview_canonical_duplicate(
        storage, library_root=library_root,
        candidate_placement_id=candidate_placement_id,
        keeper_placement_id=keeper_placement_id,
    )
    if final.status != "unreferenced":
        return CanonicalDedupExecutionResult(
            "blocked", final.reason, catalogue, backups, receipts,
        )
    if not retire:
        return CanonicalDedupExecutionResult(
            "ready", "all_references_relinked", catalogue, backups, receipts,
        )
    try:
        outcome = retire_verified_canonical_duplicate(
            storage, library_root=library_root,
            candidate_placement_id=candidate_placement_id,
            keeper_placement_id=keeper_placement_id,
            shared_lock=shared_lock,
        )
    except (ValueError, sqlite3.Error, OSError):
        return CanonicalDedupExecutionResult(
            "blocked", "retirement_revalidation_failed",
            catalogue, backups, receipts,
        )
    if not _keeper_verified(
        storage, library_root=library_root,
        keeper_placement_id=keeper_placement_id,
    ):
        return CanonicalDedupExecutionResult(
            "blocked", "keeper_postcheck_failed",
            catalogue, backups, receipts, outcome.retired_placement,
            outcome.removed_file, True,
        )
    return CanonicalDedupExecutionResult(
        "cleanup_pending" if outcome.cleanup_pending else "completed",
        "cleanup_pending" if outcome.cleanup_pending else "verified",
        catalogue, backups, receipts, outcome.retired_placement,
        outcome.removed_file, outcome.cleanup_pending,
    )


__all__ = ["CanonicalDedupExecutionResult", "execute_canonical_duplicate"]
