"""P1 #2854: bounded physical byte snapshot for advisory USB content review.

No USB writes, entitlement grant or executable token. Only the single
identity-scoped local file is opened, using O_NOFOLLOW and lexical parent
checks. A positive snapshot is NOT a pinned inode, rights certificate or
authorization for any later operation. Caller runs off the GTK thread.
"""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat
from typing import Mapping

from .device_usb_local_source import _safe_scope
from .device_usb_preview import UsbLocalArtifact, _sha


MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_RUN_SNAPSHOT_BYTES = 64 * 1024 * 1024
_READ_CHUNK = 1024 * 1024


def snapshot_local_usb_content(
    *, identity_id: str, identity_root: Path,
    artifact: Mapping[str, object], placement: Mapping[str, object],
    local: UsbLocalArtifact | None, max_bytes: int = MAX_SNAPSHOT_BYTES,
) -> bytes | None:
    """Read an exact bounded owner-local file, verifying SHA and inode twice.

    The earlier local proof is advisory and cannot stand in for this fresh
    byte check. A concurrent replacement, path escape, mutation, stale SHA,
    symlink or oversized payload yields None, never unverified bytes.
    """
    if (type(identity_id) is not str or not identity_id
            or not isinstance(identity_root, Path) or not identity_root.is_absolute()
            or not isinstance(artifact, Mapping)
            or not isinstance(placement, Mapping)
            or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_SNAPSHOT_BYTES
            or type(local) is not UsbLocalArtifact
            or local.verified is not True
            or local.identity_id != identity_id
            or placement.get("identity_id") != identity_id
            or placement.get("state") != "present"):
        return None
    artifact_id = artifact.get("artifact_id")
    digest = artifact.get("sha256")
    size = artifact.get("size")
    path_text = placement.get("path")
    if (type(artifact_id) is not str or not artifact_id
            or placement.get("artifact_id") != artifact_id
            or not _sha(digest) or local.sha256 != digest
            or type(size) is not int or not 0 < size <= max_bytes
            or artifact.get("kind") != "file"
            or (artifact_id.startswith("sha256:")
                and artifact_id != "sha256:" + digest)
            or type(path_text) is not str or not path_text.startswith("/")):
        return None

    path = Path(path_text)
    try:
        if not _safe_scope(identity_root, path):
            return None
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            return None
        if not hasattr(os, "O_NOFOLLOW"):
            return None
        fd = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino, opened.st_size,
                        opened.st_mtime_ns, opened.st_ctime_ns)
                    != (before.st_dev, before.st_ino, before.st_size,
                        before.st_mtime_ns, before.st_ctime_ns)):
                return None
            chunks: list[bytes] = []
            total = 0
            h = sha256()
            while total < size:
                chunk = os.read(fd, min(_READ_CHUNK, size - total))
                if not chunk:
                    return None
                total += len(chunk)
                h.update(chunk)
                chunks.append(chunk)
            if os.read(fd, 1) or total != size or h.hexdigest() != digest:
                return None
            after = os.fstat(fd)
            if ((after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns)
                    != (opened.st_dev, opened.st_ino, opened.st_size,
                        opened.st_mtime_ns, opened.st_ctime_ns)):
                return None
        finally:
            os.close(fd)
        if not _safe_scope(identity_root, path):
            return None
        final = path.lstat()
        if ((final.st_dev, final.st_ino, final.st_size,
             final.st_mtime_ns, final.st_ctime_ns)
                != (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns)
                or not stat.S_ISREG(final.st_mode)):
            return None
        return b"".join(chunks)
    except (OSError, ValueError, OverflowError):
        return None


__all__ = [
    "MAX_SNAPSHOT_BYTES", "MAX_RUN_SNAPSHOT_BYTES",
    "snapshot_local_usb_content",
]
