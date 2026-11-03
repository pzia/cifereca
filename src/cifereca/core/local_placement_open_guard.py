"""P0 #2797/#2847: no-follow local book open across profile-owned roots.

The SQLite owner is not permission to follow a symlink to another profile.
Legacy/manual explicit Placement paths outside the configured library remain
readable (no new root restriction). No provider I/O, transfer or file mutation.
"""
from __future__ import annotations

import os
from pathlib import Path


def scoped_existing_placement_path(service, placement, *, identity_id: str) -> Path:
    """Resolve without symlinks and refuse another configured profile's roots.

    The active library root and optional separate Kobo backup root may be
    trusted mount points. Storage.placement_put already stores a resolved
    absolute path; if its path later becomes a symlink, opening must fail
    instead of silently changing its physical owner.
    """
    stored = placement.get("path")
    if type(stored) is not str or not stored:
        raise ValueError("local_placement_path_unavailable")
    raw = Path(stored).expanduser()
    if not raw.is_absolute() or ".." in raw.parts:
        raise ValueError("local_placement_path_unavailable")

    rows = service.config.identities()
    if not any(row.get("id") == identity_id for row in rows):
        raise ValueError("local_placement_identity_unknown")
    owner = placement.get("identity_id")
    if owner in (None, ""):
        # An unowned v0.7 path belongs solely to the default identity. It
        # must not become a synthetic shared right between two profiles.
        if not rows or rows[0].get("id") != identity_id:
            raise ValueError("local_placement_legacy_scope_unknown")
    elif owner != identity_id:
        raise ValueError("local_placement_identity_mismatch")

    try:
        # Do NOT follow a substituted file or ancestor symlink after the
        # Placement was indexed. A configured mount is still usable because
        # the persisted path was canonicalized by Storage.placement_put.
        resolved = raw.resolve(strict=True)
        if resolved != raw:
            raise ValueError("local_placement_symlink")

        library_root = Path(service.library_dir()).expanduser().resolve(strict=False)
        backup_setting = service.config.get("kobo_backup.directory", "")
        if backup_setting and type(backup_setting) is not str:
            raise ValueError("local_placement_backup_root_invalid")
        if type(backup_setting) is str and backup_setting.strip():
            backup_root = Path(os.path.expandvars(os.path.expanduser(
                backup_setting.strip(),
            )))
            if not backup_root.is_absolute():
                backup_root = library_root / backup_root
            backup_root = backup_root.resolve(strict=False)
        else:
            backup_root = library_root

        # An Alice Placement pointing to Bob's registered physical directory
        # (by corrupt/imported SQLite, or after relocation) is not Alice's
        # file. Unlike a blanket library-root restriction, this preserves
        # historical explicitly owned local files on external drives.
        for row in rows:
            if row.get("id") == identity_id:
                continue
            other = row.get("directory_name")
            if type(other) is not str or not other:
                raise ValueError("local_placement_foreign_owner_unknown")
            if (
                resolved.is_relative_to(library_root / other)
                or resolved.is_relative_to(backup_root / other / "kobo")
            ):
                raise ValueError("local_placement_foreign_owner_root")
        return resolved
    except (OSError, RuntimeError) as exc:
        raise ValueError("local_placement_path_unavailable") from exc


__all__ = ["scoped_existing_placement_path"]
