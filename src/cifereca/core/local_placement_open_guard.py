"""P0 #2797/#2847: Core-only path scope for opening an existing local Placement.

The SQLite owner is not permission to follow a symlink to another profile.
No provider calls, migrations, transfers, downloads, or filesystem mutation.
"""
from __future__ import annotations

from pathlib import Path

from .library_audit_roots import configured_identity_roots


def scoped_existing_placement_path(service, placement, *, identity_id: str) -> Path:
    """Return a present path inside the exact configured owner root, no-follow.

    Explicitly owned Placements may live in the library identity directory or
    the separate configured Kobo identity/kobo root. A legacy unowned Placement
    may live elsewhere under the default library root, but not inside a
    registered *different* identity's directory.
    """
    stored = placement.get("path")
    if type(stored) is not str or not stored:
        raise ValueError("local_placement_path_unavailable")
    raw = Path(stored).expanduser()
    if not raw.is_absolute() or ".." in raw.parts:
        raise ValueError("local_placement_path_unavailable")
    rows = service.config.identities()
    identity = next((row for row in rows if row.get("id") == identity_id), None)
    if identity is None:
        raise ValueError("local_placement_identity_unknown")

    owner = placement.get("identity_id")
    legacy = owner in (None, "")
    if legacy:
        # Only the first profile owns historical NULL identity placements.
        if not rows or rows[0].get("id") != identity_id:
            raise ValueError("local_placement_legacy_scope_unknown")
        candidates = (service.library_dir(),)
    else:
        if owner != identity_id:
            raise ValueError("local_placement_identity_mismatch")
        candidates = configured_identity_roots(
            service.config, identity_id,
        )[identity_id]

    for candidate in candidates:
        try:
            anchor = Path(candidate).expanduser()
            if not legacy:
                # The configured library/backup root itself may be a trusted
                # mount symlink, but the appended identity component may not
                # be redirected to another owner's physical directory.
                owner_component = (
                    anchor if anchor.name == identity["directory_name"]
                    else anchor.parent
                )
                if (owner_component.name != identity["directory_name"]
                        or owner_component.is_symlink()):
                    continue
            physical_anchor = anchor.resolve(strict=True)
            relative = raw.relative_to(physical_anchor)
            if not relative.parts:
                continue
            if legacy:
                # A NULL owner is assigned to the default identity only; it
                # cannot adopt an existing other profile's directory.
                others = {
                    row.get("directory_name") for row in rows
                    if row.get("id") != identity_id
                }
                if relative.parts[0] in others:
                    continue
            current = physical_anchor
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError("local_placement_symlink")
            resolved = raw.resolve(strict=True)
            if resolved.is_relative_to(physical_anchor):
                return resolved
        except (OSError, ValueError, RuntimeError):
            continue
    raise ValueError("local_placement_path_out_of_scope")


__all__ = ["scoped_existing_placement_path"]
