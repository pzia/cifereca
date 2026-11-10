"""Core-local file scope helpers for legacy read-only candidates.

A path under a reader directory is a locator, never a right or evidence of
canonical acquisition. Caller owns its provider-specific root selection.
"""
from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Callable

from ..library_files import safe_library_component


def checked_profile_roots(service, identity: str, *,
                          roots_for_identity: Callable) -> tuple[Path, Path]:
    """Refuse unknown or overlapping profile directories before local scan."""
    if (type(identity) is not str or not 0 < len(identity) <= 1024
            or any(char in identity for char in ("\0", "\n", "\r"))):
        raise ValueError("unknown_identity")
    profile = service.identity_by_id(identity)
    if profile is None:
        raise ValueError("unknown_identity")
    library, target = roots_for_identity(service, identity)
    folder = safe_library_component(profile.directory_name or identity, "default")
    if any(
        other.identity_id != identity
        and safe_library_component(
            other.directory_name or other.identity_id, "default",
        ) == folder
        for other in getattr(service, "identities", ())
    ):
        raise ValueError("ambiguous_identity_directory")
    return (target, library / folder)


def path_within_roots(path: object, roots: object) -> bool:
    """Historical lexical membership check; never follow as proof of bytes."""
    if type(path) is not str or not path or "\0" in path:
        return False
    value = Path(path)
    if not value.is_absolute() or ".." in value.parts:
        return False
    value = Path(os.path.abspath(value))
    return any(value != root and value.is_relative_to(root) for root in roots)


__all__ = ["checked_profile_roots", "path_within_roots"]
