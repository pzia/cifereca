"""Private root capabilities from Core-owned normalized configuration (#2565).

This helper does no filesystem I/O. A future broker must also revalidate its
identity/configuration context and reject an in-progress directory migration.
The scanner remains responsible for no-follow opens and filesystem races.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..library_files import safe_library_component


def configured_identity_roots(config, identity_id: str) -> dict[str, tuple[Path, ...]]:
    """Select one declared identity; never authorize a global or legacy root.

    Use Config.identities()'s normalized directory_name, as acquisition does.
    Include a separately configured Kobo target, limited to its identity/kobo
    subtree. Check ALL identities for overlaps, even when only one is selected.
    Labels, source placements, ISBN, account names and filenames are not inputs.
    No mkdir, resolve, exists/stat, provider, Storage or service startup.
    """
    if type(identity_id) is not str or not identity_id or len(identity_id) > 1024:
        raise ValueError('invalid_reconciliation_identity_scope')
    rows = config.identities()
    if type(rows) is not list or not 1 <= len(rows) <= 128:
        raise ValueError('invalid_reconciliation_identity_scope')

    def directory(raw, default, parent=None):
        if raw is not None and type(raw) is not str:
            raise ValueError('invalid_reconciliation_root_configuration')
        text = (raw or default).strip()
        if not text or '\0' in text:
            raise ValueError('invalid_reconciliation_root_configuration')
        path = Path(os.path.expandvars(os.path.expanduser(text)))
        if not path.is_absolute():
            path = (Path.home() if parent is None else parent) / path
        if path == Path('/') or '..' in path.parts:
            raise ValueError('invalid_reconciliation_root_configuration')
        return path

    library = directory(config.get('library.directory', '~/EBooks'), '~/EBooks')
    raw_backup = config.get('kobo_backup.directory', '')
    if raw_backup is not None and type(raw_backup) is not str:
        raise ValueError('invalid_reconciliation_root_configuration')
    backup = directory(raw_backup, '', library) if raw_backup and raw_backup.strip() else library
    roots = []
    owners = set()
    for row in rows:
        if type(row) is not dict:
            raise ValueError('invalid_reconciliation_identity_scope')
        owner, component = row.get('id'), row.get('directory_name')
        if (type(owner) is not str or not owner or len(owner) > 1024
                or owner in owners or type(component) is not str
                or not component or len(component) > 255
                or safe_library_component(component, '') != component):
            raise ValueError('invalid_reconciliation_identity_scope')
        owners.add(owner)
        for path in (library/component, backup/component/'kobo'):
            for other_owner, other_path in roots:
                if other_owner != owner and (path.is_relative_to(other_path) or other_path.is_relative_to(path)):
                    raise ValueError('reconciliation_identity_root_collision')
            roots.append((owner, path))
    if identity_id not in owners:
        raise ValueError('unknown_reconciliation_identity')
    selected = []
    for owner, path in roots:
        if owner != identity_id or any(path.is_relative_to(parent) for parent in selected):
            continue
        selected = [child for child in selected if not child.is_relative_to(path)]
        selected.append(path)
    return {identity_id: tuple(selected)}
