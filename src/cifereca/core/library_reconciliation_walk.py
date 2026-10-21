"""Bounded, offline identity-root traversal for library reconciliation (#2565).

This is only an *untrusted candidate enumeration*, never a verified SHA,
ownership, entitlement, or evidence that a file can be repaired. Callers must
reopen every file by no-follow directory descriptors and revalidate its stat
before SHA/EPUB inspection. Pathnames and identities stay private to Core;
diagnostics may publish only the closed status/reason and bounded counts.

No mkdir, cache, SQLite connection, provider/Adobe call or filesystem mutation.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Callable

_ALLOWED_SUFFIXES = frozenset({".epub", ".pdf"})
_MAX_ENTRIES = 100_000
_MAX_FILES = 20_000
_MAX_DEPTH = 64
_MAX_FILE_BYTES = 512 * 1024 * 1024
_REASONS = frozenset({
    "entry_budget", "file_budget", "depth_budget", "directory_unavailable",
    "cancelled", "unsupported_platform",
})


@dataclass(frozen=True, slots=True, repr=False)
class FileCandidate:
    """Private observation, not an authoritative or shareable book record."""

    path: Path
    size: int
    device: int
    inode: int
    mtime_ns: int


@dataclass(frozen=True, slots=True, repr=False)
class WalkResult:
    candidates: tuple[FileCandidate, ...]
    entries_inspected: int
    directories_inspected: int
    reasons: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.reasons

    def redacted_summary(self) -> dict[str, object]:
        """Fixed allowlist; neither paths, SHA, filenames nor account IDs."""
        return {
            "status": "complete" if self.complete else "incomplete_scan",
            "candidate_count": len(self.candidates),
            "entries_inspected": self.entries_inspected,
            "directories_inspected": self.directories_inspected,
            "incomplete_reasons": list(self.reasons),
            "read_only": True,
            "acquisition_authorized": False,
            "mutation_authorized": False,
        }


def walk_identity_root(
    root: Path, *, max_entries: int = 8192, max_files: int = 4096,
    max_depth: int = 24, cancelled: Callable[[], bool] | None = None,
) -> WalkResult:
    """Enumerate only regular EPUB/PDF candidates below one explicit root.

    Each ancestor is opened from '/' with O_NOFOLLOW|O_DIRECTORY; descendants
    are likewise opened relative to the already-open parent fd. Symlinked
    entries, including dangling links and directory junctions, are ignored.
    A changed directory while scanning can create an unreachable pathname,
    but cannot cause this scanner to open a file outside the root: no leaf
    content is opened here. The later SHA worker must reopen safely.

    Budgets are global to this one call. A hit is reported as incomplete, not
    as proof that omitted local files or provider rights do not exist.
    """
    if any(type(value) is not int or not 1 <= value <= bound for value, bound in (
        (max_entries, _MAX_ENTRIES),
        (max_files, _MAX_FILES),
        (max_depth, _MAX_DEPTH),
    )):
        raise ValueError("invalid_reconciliation_walk_budget")
    if not isinstance(root, Path) or not root.is_absolute() or ".." in root.parts:
        raise ValueError("invalid_reconciliation_identity_root")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        return WalkResult((), 0, 0, ("unsupported_platform",))

    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    candidates: list[FileCandidate] = []
    reasons: set[str] = set()
    entries = 0
    directories = 0

    def stopped() -> bool:
        return cancelled is not None and cancelled() is True

    def visit(fd: int, path: Path, depth: int) -> None:
        nonlocal entries, directories
        if stopped():
            reasons.add("cancelled")
            return
        directories += 1
        try:
            with os.scandir(fd) as iterator:
                for entry in iterator:
                    if stopped():
                        reasons.add("cancelled")
                        return
                    if entries >= max_entries:
                        reasons.add("entry_budget")
                        return
                    entries += 1
                    # Do not use is_file() as evidence for a non-symlinked
                    # regular file: stat explicitly without following links.
                    try:
                        info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                    except OSError:
                        reasons.add("directory_unavailable")
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        if depth >= max_depth:
                            reasons.add("depth_budget")
                            continue
                        try:
                            child = os.open(entry.name, flags, dir_fd=fd)
                        except OSError:
                            reasons.add("directory_unavailable")
                            continue
                        try:
                            visit(child, path / entry.name, depth + 1)
                        finally:
                            os.close(child)
                        if "cancelled" in reasons or "entry_budget" in reasons or "file_budget" in reasons:
                            return
                    elif (
                        stat.S_ISREG(info.st_mode)
                        and Path(entry.name).suffix.casefold() in _ALLOWED_SUFFIXES
                        and 0 < info.st_size <= _MAX_FILE_BYTES
                    ):
                        if len(candidates) >= max_files:
                            reasons.add("file_budget")
                            return
                        candidates.append(FileCandidate(
                            path / entry.name, info.st_size, info.st_dev,
                            info.st_ino, info.st_mtime_ns,
                        ))
        except OSError:
            reasons.add("directory_unavailable")

    fd = -1
    try:
        try:
            fd = os.open("/", flags)
            for part in root.parts[1:]:
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
        except OSError:
            reasons.add("directory_unavailable")
            return WalkResult((), entries, directories, tuple(sorted(reasons)))
        visit(fd, root, 0)
    finally:
        if fd >= 0:
            os.close(fd)
    assert reasons <= _REASONS
    return WalkResult(tuple(candidates), entries, directories, tuple(sorted(reasons)))


__all__ = ["FileCandidate", "WalkResult", "walk_identity_root"]
