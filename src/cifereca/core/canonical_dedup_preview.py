"""#3185: read-only, fail-closed preview of a canonical physical duplicate.

Preview is advisory evidence, NOT a permission to mutate. Every subsequent
commit must obtain the common acquisition lock, revalidate exact profile,
SHA, dependency graph and inode, and use the crash-safe Core writer.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from .canonical_publication import _same_file
from .convergence_verified_presence import _verified_copy
from .duplicate_maintenance_guard import candidate_has_active_dependencies_in_db


@dataclass(frozen=True, slots=True)
class CanonicalDedupPreview:
    """Closed diagnostic with no private paths/accounts or mutation authority."""

    status: str
    reason: str
    candidate_placement_id: str
    keeper_placement_id: str
    catalogue_references: int = 0
    backup_references: int = 0
    other_or_unknown_references: bool = False

    @property
    def may_attempt_retirement(self) -> bool:
        """Not authorization: re-evaluate everything during the real commit."""
        return self.status == "unreferenced"


def preview_canonical_duplicate(
    storage, *, library_root: Path, candidate_placement_id: str,
    keeper_placement_id: str,
) -> CanonicalDedupPreview:
    """Inspect a *particular* pair without modifying filesystem or SQLite.

    Returns 'unreferenced' ONLY if two regular files re-hash under one unique
    profile and the full destructive dependency guard proves no references.
    Referenced copies remain ineligible even if both have the same SHA. A
    failure to classify ownership, JSON or FS is a closed diagnostic, never
    evidence for deletion.
    """
    def report(status: str, reason: str, *, catalogue: int = 0,
               backup: int = 0, other: bool = False) -> CanonicalDedupPreview:
        return CanonicalDedupPreview(
            status, reason, candidate_placement_id, keeper_placement_id,
            catalogue, backup, other,
        )

    if (not isinstance(library_root, Path)
            or type(candidate_placement_id) is not str
            or not 0 < len(candidate_placement_id) <= 256
            or type(keeper_placement_id) is not str
            or not 0 < len(keeper_placement_id) <= 256
            or candidate_placement_id == keeper_placement_id):
        return report("blocked", "invalid_request")
    try:
        if storage.schema_version() < 14:
            return report("blocked", "schema_unavailable")
        with storage._lock, storage._connect() as db:
            # A consistent read snapshot even when a sync publishes during
            # this (potentially expensive) offline physical verification.
            db.execute("BEGIN")
            query = (
                "SELECT path,identity_id,artifact_id,state,provider_id,"
                "source_item_id FROM placements WHERE placement_id=?"
            )
            candidate = db.execute(query, (candidate_placement_id,)).fetchone()
            keeper = db.execute(query, (keeper_placement_id,)).fetchone()
            if candidate is None or keeper is None:
                return report("blocked", "placement_missing")
            if (type(candidate[0]) is not str or not candidate[0]
                    or type(keeper[0]) is not str or not keeper[0]
                    or candidate[0] == keeper[0]
                    or candidate[1] != keeper[1] or candidate[2] != keeper[2]
                    or candidate[3] != "present" or keeper[3] != "present"
                    or candidate[4:] != (None, None)
                    or keeper[4:] != (None, None)
                    or type(candidate[1]) is not str or not candidate[1]
                    or type(candidate[2]) is not str):
                return report("blocked", "scope_mismatch")
            artifact = db.execute(
                "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
                (candidate[2],),
            ).fetchone()
            if (artifact is None or type(artifact[0]) is not str
                    or len(artifact[0]) != 64
                    or any(ch not in "0123456789abcdef" for ch in artifact[0])
                    or candidate[2] != "sha256:" + artifact[0]
                    or type(artifact[1]) is not int or artifact[1] <= 0):
                return report("blocked", "artifact_unverified")
            profiles = db.execute(
                "SELECT identity_id,directory_name FROM identities"
            ).fetchall()
            folders = [folder for identity, folder in profiles
                       if identity == candidate[1]]
            if (len(folders) != 1 or type(folders[0]) is not str
                    or not folders[0]
                    or sum(folder == folders[0] for _, folder in profiles) != 1):
                return report("blocked", "profile_ambiguous")
            reserved = frozenset(
                folder for _, folder in profiles if type(folder) is str and folder
            )
            observations = []
            for raw in (candidate[0], keeper[0]):
                path = Path(raw)
                try:
                    before = os.stat(path, follow_symlinks=False)
                except OSError:
                    return report("blocked", "physical_evidence_changed")
                if (not stat.S_ISREG(before.st_mode)
                        or not _verified_copy(
                            raw, root=library_root, owner_directory=folders[0],
                            registered_directories=reserved, size=artifact[1],
                            sha256=artifact[0],
                        ) or not _same_file(path, before)):
                    return report("blocked", "physical_evidence_changed")
                observations.append((path, before))

            # Count the exact references for human-readable diagnostics.
            # A generic/unknown path elsewhere still prevents retirement.
            catalogue = db.execute(
                "SELECT COUNT(*) FROM catalog_entries WHERE placement_id=?",
                (candidate_placement_id,),
            ).fetchone()[0]
            backup = db.execute(
                "SELECT COUNT(*) FROM backup_items WHERE local_path=?",
                (candidate[0],),
            ).fetchone()[0]
            if catalogue > 100_000 or backup > 100_000:
                return report("blocked", "dependency_budget_exceeded")
            active = candidate_has_active_dependencies_in_db(
                db, placement_id=candidate_placement_id, path=candidate[0],
            )
            if any(not _same_file(path, before) for path, before in observations):
                return report("blocked", "physical_evidence_changed")
            if active:
                return report(
                    "blocked", "active_references", catalogue=catalogue,
                    backup=backup, other=True,  # Other references cannot be excluded here.
                )
            return report("unreferenced", "verified_no_active_references")
    except (OSError, ValueError, TypeError, RuntimeError, RecursionError):
        return report("blocked", "inspection_unavailable")
    except Exception:
        # Read-only previews must not turn a storage/JSON/SQLite failure into
        # false evidence that physical copies can be removed.
        return report("blocked", "inspection_unavailable")


__all__ = ["CanonicalDedupPreview", "preview_canonical_duplicate"]
