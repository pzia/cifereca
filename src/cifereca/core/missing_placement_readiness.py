"""Private, read-only readiness for a single missing Placement (#2847).

A missing SQLite state is not permission to retire history. This probe only
identifies safe-looking candidates for a later explicit Core transaction.
It never deletes, relinks, scans inventory or calls provider/network code.
"""
from __future__ import annotations

import os
from pathlib import Path

from .duplicate_maintenance_guard import candidate_has_active_dependencies_in_db


def _physical_absence(identity_root: Path, stored_path: str) -> str:
    """Classify a missing leaf only under accessible no-follow directories."""
    try:
        path = Path(stored_path)
        if (not path.is_absolute() or ".." in path.parts
                or not identity_root.is_absolute()):
            return "path_out_of_scope"
        relative = path.relative_to(identity_root)
        if not relative.parts or any(p in {"", ".", ".."} for p in relative.parts):
            return "path_out_of_scope"
    except (TypeError, ValueError):
        return "path_out_of_scope"
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    fds: list[int] = []
    try:
        fd = os.open(identity_root, flags)
        fds.append(fd)
        for component in relative.parts[:-1]:
            fd = os.open(component, flags, dir_fd=fd)
            fds.append(fd)
        try:
            # Never follow a dangling link: it is still a physical entry.
            os.stat(relative.parts[-1], dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return "absent"
        return "path_exists"
    except OSError:
        # Permission failures, absent parents and unmounted roots are not
        # verified leaf absence. Do not guess which condition occurred.
        return "parent_or_root_unavailable"
    finally:
        for fd in reversed(fds):
            os.close(fd)


def preview_missing_placement_retirement(
    storage, *, placement_id: str, identity_id: str, library_root: Path,
) -> dict[str, object]:
    """Return closed private status; no raw path, SHA, title or account values.

    A READY result is never an authorization to DELETE. Any future mutation
    must independently revalidate consent, root identity, physical absence,
    SQLite references and placement revision under its own transaction.
    """
    def blocked(reason: str, *, catalogue_rows: int = 0) -> dict[str, object]:
        return {
            "status": "blocked", "reason": reason,
            "catalogue_rows": catalogue_rows,
            "requires_confirmation": True,
        }

    if (type(placement_id) is not str or not 0 < len(placement_id) <= 256
            or type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or not isinstance(library_root, Path) or not library_root.is_absolute()):
        return blocked("invalid_scope")
    try:
        # The configured root itself may be a trusted mount/symlink, but it
        # must currently resolve to an accessible directory.
        root = library_root.resolve(strict=True)
        if not root.is_dir():
            return blocked("root_unavailable")
    except OSError:
        return blocked("root_unavailable")

    try:
        with storage._lock, storage._connect() as db:
            owner = db.execute(
                "SELECT directory_name FROM identities WHERE identity_id=?",
                (identity_id,),
            ).fetchone()
            if owner is None or type(owner[0]) is not str:
                return blocked("unknown_identity")
            directory = owner[0]
            if (directory in {"", ".", ".."} or "/" in directory
                    or "\\" in directory):
                return blocked("invalid_identity_directory")
            row = db.execute(
                "SELECT path,identity_id,state FROM placements "
                "WHERE placement_id=?",
                (placement_id,),
            ).fetchone()
            if row is None:
                return blocked("placement_absent")
            path, actual_owner, state = row
            if actual_owner != identity_id:
                return blocked("identity_mismatch")
            if state != "missing":
                return blocked("not_missing")
            if type(path) is not str or not path:
                return blocked("path_out_of_scope")

            # Exact private root scope. Never resolve the stored path:
            # following a symlink would turn a foreign target into evidence.
            identity_root = root / directory
            physical = _physical_absence(identity_root, path)
            if physical != "absent":
                return blocked(physical)

            linked_count = db.execute(
                "SELECT COUNT(*) FROM catalog_entries WHERE placement_id=?",
                (placement_id,),
            ).fetchone()[0]
            # Reuse the fail-closed Core dependency checker, including
            # backup_items, all canonical metadata, variants, recovery
            # checkpoints and private user declarations. A linked catalogue
            # requires its own explicit unlink plan; do not auto-cascade.
            if candidate_has_active_dependencies_in_db(
                db, placement_id=placement_id, path=path,
            ):
                return blocked("dependent_references", catalogue_rows=int(linked_count))
            return {
                "status": "ready", "reason": "orphan_missing",
                "catalogue_rows": 0, "requires_confirmation": True,
            }
    except (OSError, ValueError, RuntimeError):
        return blocked("evidence_unavailable")
