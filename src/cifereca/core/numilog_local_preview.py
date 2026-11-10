"""Retired Numilog v1 local backup preview.

The historical receipt/placement reconciliation and hashing traversal have
no acquisition authority. Canonical download retains exact rights, staged
receipt and artifact verification independently. Keep the identity root guard
required by the active acquisition path.
"""
from __future__ import annotations

from pathlib import Path
import os
from typing import Any

from ..library_files import safe_library_component
from ..plugin_api import SyncResult


def _identity_root(service: Any, identity_id: str) -> tuple[Path, ...] | None:
    """Compute an identity-owned root WITHOUT mkdir(), resolve(), or scans."""
    try:
        current = service.identity_by_id(identity_id)
        if current is None:
            return None
        dirname = safe_library_component(current.directory_name or identity_id, "default")
        if any(
            other.identity_id != identity_id
            and safe_library_component(other.directory_name or other.identity_id, "default") == dirname
            for other in service.identities
        ):
            return None
        configured = service.config.get("library.directory", "~/EBooks")
        if type(configured) is not str or not configured.strip():
            return None
        configured_path = Path(os.path.expandvars(os.path.expanduser(configured)))
        if not configured_path.is_absolute():
            # Match LibraryService.library_dir() without creating directories.
            configured_path = Path.home() / configured_path
        root = Path(os.path.abspath(configured_path))
        return (root / dirname,)
    except Exception:
        return None


def preview(
    broker: Any, account: Any, source_id: str, *, verify_local_files: bool,
    _only_item_id: str | None = None,
) -> SyncResult:
    """Retired v1 preview: no provider or local filesystem operation."""
    return SyncResult(
        ok=False, changed=0, warnings=("preview_unavailable",),
        facts={"phase": "local_materialization", "status": "preview_unavailable",
               "read_only": True, "acquisition_authorized": False},
    )
