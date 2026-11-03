"""P1 #2854: physical local-source evidence for read-only USB planning.

No USB I/O, supplier request, entitlement grant, library mutation or copy.
Verifying bytes only establishes that an identity-scoped local placement
currently contains the expected SHA; it is never permission to transfer.
The executor must independently revalidate source, account/rights and target.
"""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat
from typing import Mapping

from .device_usb_preview import UsbLocalArtifact, _sha


_MAX_BOOK_BYTES = 8 * 1024**3
_READ_CHUNK = 1024 * 1024


def _safe_scope(root: Path, path: Path) -> bool:
    """All path components must be real directories, not symlink redirects."""
    if (not isinstance(root, Path) or not isinstance(path, Path)
            or not root.is_absolute() or not path.is_absolute()
            or ".." in root.parts or ".." in path.parts
            or root == path):
        return False
    try:
        inside = path.relative_to(root)
    except ValueError:
        return False
    if not inside.parts or any(part in {"", ".", ".."} for part in inside.parts):
        return False
    # Never call resolve() here: it would erase evidence that a parent was
    # swapped for a symlink. Inspect the lexical path component by component.
    current = Path(root.anchor)
    for part in root.parts[1:] + inside.parts[:-1]:
        current = current / part
        row = current.lstat()
        if not stat.S_ISDIR(row.st_mode):
            return False
    return True


def attest_local_usb_source(
    *, identity_id: str, identity_root: Path,
    artifact: Mapping[str, object], placement: Mapping[str, object],
    max_bytes: int = _MAX_BOOK_BYTES,
) -> UsbLocalArtifact | None:
    """Hash one exact owned local file into a non-authorizing preview fact.

    A missing/foreign/ambiguous placement, symlink, stale metadata, file
    mutation or SHA mismatch returns None. Never expose the path or content
    in the resulting evidence. This is a potentially expensive blocking
    operation: callers must execute it off the GTK/UI thread.
    """
    if (type(identity_id) is not str or not identity_id
            or not isinstance(identity_root, Path)
            or not isinstance(artifact, Mapping)
            or not isinstance(placement, Mapping)
            or type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_BOOK_BYTES
            or placement.get("identity_id") != identity_id
            or placement.get("state") != "present"):
        return None
    artifact_id = artifact.get("artifact_id")
    digest = artifact.get("sha256")
    expected_size = artifact.get("size")
    path_text = placement.get("path")
    kind = artifact.get("kind")
    metadata = artifact.get("metadata")
    if kind is None and type(metadata) is dict:
        kind = metadata.get("artifact_kind")
    if (type(artifact_id) is not str or not artifact_id
            or placement.get("artifact_id") != artifact_id
            or not _sha(digest)
            or type(expected_size) is not int
            or not 0 < expected_size <= max_bytes
            or kind != "file"
            or type(path_text) is not str
            or not path_text.startswith("/")):
        return None
    path = Path(path_text)
    try:
        if not _safe_scope(identity_root, path):
            return None
        leaf = path.lstat()
        if not stat.S_ISREG(leaf.st_mode) or leaf.st_size != expected_size:
            return None
        # O_NOFOLLOW prevents the last component from switching to a
        # symlink between lstat() and open(). Parent ancestry is checked
        # before *and* after the read; future writes need stronger openat2
        # or a pinned directory FD and a fresh identity/rights check.
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if not hasattr(os, "O_NOFOLLOW"):
            return None
        fd = os.open(path, flags | os.O_NOFOLLOW)
        try:
            first = os.fstat(fd)
            if (not stat.S_ISREG(first.st_mode)
                    or first.st_size != expected_size
                    or first.st_dev != leaf.st_dev
                    or first.st_ino != leaf.st_ino):
                return None
            h = sha256()
            count = 0
            while True:
                chunk = os.read(fd, min(_READ_CHUNK, max_bytes + 1 - count))
                if not chunk:
                    break
                count += len(chunk)
                if count > expected_size or count > max_bytes:
                    return None
                h.update(chunk)
            after = os.fstat(fd)
            if (count != expected_size
                    or first.st_dev != after.st_dev
                    or first.st_ino != after.st_ino
                    or first.st_size != after.st_size
                    or first.st_mtime_ns != after.st_mtime_ns
                    or first.st_ctime_ns != after.st_ctime_ns):
                return None
        finally:
            os.close(fd)
        if not _safe_scope(identity_root, path):
            return None
        final = path.lstat()
        if (not stat.S_ISREG(final.st_mode)
                or (final.st_dev, final.st_ino, final.st_size,
                    final.st_mtime_ns, final.st_ctime_ns)
                != (first.st_dev, first.st_ino, first.st_size,
                    first.st_mtime_ns, first.st_ctime_ns)
                or h.hexdigest() != digest):
            return None
        return UsbLocalArtifact(identity_id=identity_id,
                                sha256=digest, verified=True)
    except (OSError, ValueError, OverflowError):
        return None


__all__ = ["attest_local_usb_source"]
