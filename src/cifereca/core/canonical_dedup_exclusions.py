"""P1 #4496: closed identity-scoped exclusions for ordinary deduplication.

A future reader Outbox is a *delivery copy*, never a keeper or retiree of
ordinary library dedup. This module is read-only and has no preference-based
override. Ordinary destructive cleanup remains independently suspended.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping


# The reserved prefix is projected beneath EVERY registered identity. No
# Outbox is created, no USB target is discovered and no deletion is granted.
_DEFAULT_EXCLUDED_RELATIVE = ("Outbox",)


def ordinary_dedup_path_excluded(
    path: object, *, library_root: Path, identity_id: str,
    owner_directory: str,
    additional_by_identity: Mapping[str, tuple[str, ...]] | None = None,
) -> bool:
    """True means excluded OR unprovable; false means no exclusion *alone*.

    Internal future per-device prefixes may be supplied by Core, never by a
    provider, UI checkbox, untrusted plan or user preference. An exclusion
    applies to either a candidate or a keeper, before any relink/retirement.
    """
    if (not isinstance(path, (str, Path))
            or not isinstance(library_root, Path)
            or not library_root.is_absolute()
            or any(p in ("", ".", "..") for p in library_root.parts)
            or type(identity_id) is not str or not identity_id
            or type(owner_directory) is not str
            or not owner_directory or owner_directory in (".", "..")
            or "/" in owner_directory or "\\" in owner_directory):
        return True
    candidate = Path(path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        return True
    owner = library_root / owner_directory
    try:
        relative = candidate.relative_to(owner)
    except ValueError:
        return True
    if not relative.parts:
        return True

    # Never authorize a plan through a symbolic parent or a renamed alias.
    # Check every existing directory component, including the excluded one.
    for node in (library_root, owner, *(owner / Path(*relative.parts[:i])
                                      for i in range(1, len(relative.parts) + 1))):
        if node.is_symlink():
            return True

    prefixes: tuple[str, ...] = _DEFAULT_EXCLUDED_RELATIVE
    if additional_by_identity is not None:
        if not isinstance(additional_by_identity, Mapping):
            return True
        additional = additional_by_identity.get(identity_id, ())
        if type(additional) not in (tuple, list) or len(additional) > 64:
            return True
        for name in additional:
            if (type(name) is not str or not name
                    or name.startswith("/") or "\\" in name
                    or any(part in ("", ".", "..") for part in name.split("/"))):
                return True
        prefixes = prefixes + tuple(additional)
    for prefix in prefixes:
        parts = tuple(prefix.split("/"))
        if relative.parts[:len(parts)] == parts:
            return True
    return False


def ordinary_dedup_paths_excluded(
    paths: tuple[object, ...], *, library_root: Path,
    identity_id: str, owner_directory: str,
    additional_by_identity: Mapping[str, tuple[str, ...]] | None = None,
) -> bool:
    """Apply identical exclusions to source, keeper and intermediate paths."""
    if type(paths) is not tuple or not paths:
        return True
    return any(
        ordinary_dedup_path_excluded(
            path, library_root=library_root, identity_id=identity_id,
            owner_directory=owner_directory,
            additional_by_identity=additional_by_identity,
        )
        for path in paths
    )


__all__ = ["ordinary_dedup_path_excluded", "ordinary_dedup_paths_excluded"]
