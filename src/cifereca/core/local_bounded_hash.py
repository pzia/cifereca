"""Provider-neutral bounded no-follow hashing for existing local files.

A byte hash is only an integrity observation; it never grants a right,
authorizes an acquisition, or follows a symbolic link across identities.
Both canonical Numilog read-only preflight and offline receipt checks use it.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

_MAX_FILE_BYTES = 512 * 1024 * 1024

@dataclass
class _HashBudget:
    remaining: int

_HASH_BUDGET_EXHAUSTED = object()


def _read_hash_within(
    path_text: object, roots: tuple[Path, ...], *,
    budget: _HashBudget | None = None,
) -> tuple[str, int, str] | None | object:
    """Hash a regular file via no-follow directory descriptors below one root.

    Reject missing, legacy/out-of-identity, symlinked or non-regular paths.
    A directory swap cannot turn the checked child into an external path:
    every directory component is opened with O_NOFOLLOW relative to its parent.
    """
    # Without O_NOFOLLOW, a symlinked ancestor or leaf can escape the
    # owning identity. Fail closed instead of silently dropping the flag.
    if not hasattr(os, "O_NOFOLLOW"):
        return None
    if type(path_text) is not str or not path_text or "\x00" in path_text:
        return None
    try:
        path = Path(path_text)
        if not path.is_absolute() or path.suffix.lower() not in {".epub", ".pdf"}:
            return None
        absolute = Path(os.path.abspath(path))
        candidates = []
        for root in roots:
            parent = Path(os.path.abspath(root))
            try:
                relative = absolute.relative_to(parent)
            except ValueError:
                continue
            if relative.parts and all(part not in {".", "..", ""} for part in relative.parts):
                candidates.append((len(parent.parts), parent, relative.parts))
        if not candidates:
            return None
        _, parent, pieces = max(candidates)
        # O_NOFOLLOW only protects the last path component. Resolve the
        # entire configured root from '/' using no-follow directory FDs, or
        # an ancestor link library/alice -> bob can cross identity boundaries.
        flags_dir = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open("/", flags_dir)
        try:
            for directory in parent.parts[1:]:
                newfd = os.open(directory, flags_dir, dir_fd=fd)
                os.close(fd)
                fd = newfd
            for directory in pieces[:-1]:
                newfd = os.open(directory, flags_dir, dir_fd=fd)
                os.close(fd)
                fd = newfd
            flags_file = (
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            )
            file_fd = os.open(pieces[-1], flags_file, dir_fd=fd)
            try:
                with os.fdopen(file_fd, "rb") as stream:
                    file_fd = -1
                    before = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or before.st_size < 1 or before.st_size > _MAX_FILE_BYTES
                    ):
                        return None
                    # No bytes are read if the *remaining global* hash budget
                    # cannot cover the complete file. File size is checked on
                    # the already-open, no-follow descriptor, not an unsafe
                    # path-level stat. Future records may still fit the budget.
                    if budget is not None and before.st_size > budget.remaining:
                        return _HASH_BUDGET_EXHAUSTED
                    digest = hashlib.sha256()
                    total = 0
                    while total < before.st_size:
                        chunk = min(1024 * 1024, before.st_size - total)
                        if budget is not None:
                            chunk = min(chunk, budget.remaining)
                            if chunk <= 0:
                                return _HASH_BUDGET_EXHAUSTED
                        data = stream.read(chunk)
                        if not data:
                            break
                        total += len(data)
                        if budget is not None:
                            budget.remaining -= len(data)
                        if total > _MAX_FILE_BYTES:
                            return None
                        digest.update(data)
                    after = os.fstat(stream.fileno())
                    if (
                        total != before.st_size or after.st_size != before.st_size
                        or after.st_mtime_ns != before.st_mtime_ns
                        or after.st_ctime_ns != before.st_ctime_ns
                        or after.st_dev != before.st_dev or after.st_ino != before.st_ino
                    ):
                        return None
                    return digest.hexdigest(), total, str(absolute)
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError):
        return None

