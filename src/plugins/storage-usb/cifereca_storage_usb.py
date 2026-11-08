"""Generic USB staging and explicitly scoped read-only file observation.

Core owns mount identity, authorization, managed-write receipts and publication.
This plugin never infers those facts from filesystem contents.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
from typing import Callable


_CHUNK_SIZE = 1024 * 1024
_MAX_OBSERVED = 256
_MAX_PATH = 512
_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_COMPONENT = re.compile(r"^[^/\\:\x00]+$")


@dataclass(frozen=True, slots=True)
class UsbFileObservation:
    relative_path: str
    status: str  # present, missing, unverified
    sha256: str | None = None
    size: int | None = None


def _parts(path: object) -> tuple[str, ...]:
    if type(path) is not str or not 1 <= len(path) <= _MAX_PATH or path.startswith("/"):
        raise ValueError("usb_observation_path_invalid")
    parts = tuple(path.split("/"))
    if any(p in ("", ".", "..") or not _COMPONENT.fullmatch(p)
           or not p.isprintable() for p in parts):
        raise ValueError("usb_observation_path_invalid")
    return parts


def observe_usb_files(root: Path, relative_paths: tuple[str, ...]) -> tuple[UsbFileObservation, ...]:
    """Hash explicit regular files under a caller-selected managed root.

    This is a non-authoritative read-only observation. Missing paths do not
    establish a live USB device, and no row is a transfer/removal permission.
    Core must attest/pin device identity and revalidate before any mutation.
    """
    if (not isinstance(root, Path) or not root.is_absolute()
            or type(relative_paths) is not tuple or len(relative_paths) > _MAX_OBSERVED):
        raise ValueError("usb_observation_request_invalid")
    selected = tuple((path, _parts(path)) for path in relative_paths)
    casefolded = [path.casefold() for path, _ in selected]
    if len(set(casefolded)) != len(casefolded):
        raise ValueError("usb_observation_paths_collide")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        root_fd = os.open(root, flags)
    except OSError:
        return tuple(UsbFileObservation(path, "unverified") for path, _ in selected)
    try:
        result = []
        remaining = _MAX_TOTAL_BYTES
        for path, parts in selected:
            parent_fd = os.dup(root_fd)
            file_fd = None
            try:
                for part in parts[:-1]:
                    next_fd = os.open(part, flags, dir_fd=parent_fd)
                    os.close(parent_fd)
                    parent_fd = next_fd
                try:
                    file_fd = os.open(
                        parts[-1],
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                        dir_fd=parent_fd,
                    )
                except FileNotFoundError:
                    result.append(UsbFileObservation(path, "missing"))
                    continue
                before = os.fstat(file_fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or before.st_size > remaining):
                    result.append(UsbFileObservation(path, "unverified"))
                    continue
                digest = sha256()
                size = 0
                while chunk := os.read(file_fd, _CHUNK_SIZE):
                    size += len(chunk)
                    digest.update(chunk)
                after = os.fstat(file_fd)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                    before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns) or size != before.st_size:
                    result.append(UsbFileObservation(path, "unverified"))
                else:
                    remaining -= size
                    result.append(UsbFileObservation(path, "present", digest.hexdigest(), size))
            except OSError:
                result.append(UsbFileObservation(path, "unverified"))
            finally:
                if file_fd is not None:
                    os.close(file_fd)
                os.close(parent_fd)
        return tuple(result)
    finally:
        os.close(root_fd)


class UsbMassStorageTarget:
    def write_usb_stage(self, payload: bytes, append: Callable[[bytes], None]) -> None:
        for offset in range(0, len(payload), _CHUNK_SIZE):
            append(payload[offset:offset + _CHUNK_SIZE])

    def observe_usb_files(self, root: Path, relative_paths: tuple[str, ...]) -> tuple[UsbFileObservation, ...]:
        return observe_usb_files(root, relative_paths)


class UsbMassStoragePlugin:
    def activate(self, context) -> UsbMassStorageTarget:
        return UsbMassStorageTarget()


PLUGIN = UsbMassStoragePlugin()
