"""Kobo profile path selection, isolated from retired W2 backup execution.

This computes local directory candidates only: no filesystem mutation,
supplier right, acquisition or proof of delivered bytes.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..library_files import safe_library_component


def _kobo_preview_paths(self, identity_id: str) -> tuple[Path, Path]:
    """Derive Kobo paths without creating a directory or touching metadata."""
    configured_root = os.path.expandvars(os.path.expanduser(
        str(self.config.get("library.directory", "~/EBooks") or "~/EBooks").strip()
    ))
    library_root = Path(configured_root)
    if not library_root.is_absolute():
        library_root = Path.home() / library_root

    configured = os.path.expandvars(os.path.expanduser(
        str(self.config.get("kobo_backup.directory", "") or "").strip()
    ))
    if configured:
        backup_root = Path(configured)
        if not backup_root.is_absolute():
            backup_root = library_root / backup_root
    else:
        backup_root = library_root
    identity = self.identity_by_id(identity_id)
    directory_name = identity.directory_name if identity and identity.directory_name else identity_id
    target_dir = backup_root / safe_library_component(directory_name, "default") / "kobo"
    return library_root, target_dir

