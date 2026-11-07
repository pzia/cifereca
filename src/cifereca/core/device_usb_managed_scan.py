"""P1 #2854: read-only inventory of a Linux USB volume's managed subtree.

No transfer, deletion, placement updates, rights or mount operations.
Only observe_linux_usb_mount may attest the current block device. Failure to
enumerate or hash *any* managed entry returns None, never a partial scan
marked complete. An executor must repeat mount/source/target checks under lock.
"""
from __future__ import annotations

from hashlib import sha256
from itertools import islice
import os
from pathlib import Path
import stat
import time
from typing import Mapping, Sequence

from . import device_usb_mount as linux_mount
from .device_usb_mount import observe_linux_usb_mount
from .device_usb_preview import UsbDeviceScan, UsbObservedFile, _relative


_CHUNK = 1024 * 1024
_MAX_DEPTH = 12
_MAX_FILES = 100000
_MAX_DIRS = 10000
_MAX_FILE_SIZE = 4 * 1024**3
_MAX_TOTAL_BYTES = 16 * 1024**3


def _managed_target_map(
    *, identity_id: str, device_id: str, managed: str,
    placements: Sequence[Mapping[str, object]],
) -> dict[str, str] | None:
    if type(placements) not in (list, tuple) or len(placements) > _MAX_FILES:
        return None
    result: dict[str, str] = {}
    used_ids: set[str] = set()
    for row in placements:
        if (not isinstance(row, Mapping)
                or row.get("device_id") != device_id
                or type(row.get("artifact_id")) is not str
                or not row["artifact_id"]
                or row["artifact_id"] in used_ids
                or type(row.get("metadata")) is not dict
                or row["metadata"].get("intent_identity_id") != identity_id):
            # Do not reveal another profile's historic placement via USB.
            return None
        artifact = row["artifact_id"]
        used_ids.add(artifact)
        target = row.get("target_relative_path")
        if target is None:
            continue
        if (not _relative(target)
                or not target.startswith(managed + "/")):
            return None
        key = target.casefold()
        if key in result:
            return None
        result[key] = artifact
    return result


def _regular_hash(
    directory_fd: int, name: str, original: os.stat_result,
    *, budget: int, mount_id: int,
) -> tuple[str, int] | None:
    if (not stat.S_ISREG(original.st_mode)
            or original.st_size < 0
            or original.st_size > _MAX_FILE_SIZE
            or original.st_size > budget):
        return None
    if not hasattr(os, "O_NOFOLLOW"):
        return None
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
    except OSError:
        return None
    try:
        current = os.fstat(fd)
        if (not stat.S_ISREG(current.st_mode)
                or linux_mount._fd_mount_id(fd) != mount_id
                or (current.st_dev, current.st_ino, current.st_size)
                != (original.st_dev, original.st_ino, original.st_size)):
            return None
        digest = sha256()
        consumed = 0
        while consumed < original.st_size:
            data = os.read(fd, min(_CHUNK, original.st_size - consumed))
            if not data:
                return None
            consumed += len(data)
            digest.update(data)
        # Do not trust a file that grew while being read.
        if os.read(fd, 1):
            return None
        after = os.fstat(fd)
        if (consumed != original.st_size
                or (current.st_dev, current.st_ino, current.st_size,
                    current.st_mtime_ns, current.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns)):
            return None
        return digest.hexdigest(), consumed
    except OSError:
        return None
    finally:
        os.close(fd)


def scan_managed_usb(
    *, identity_id: str, binding: Mapping[str, object],
    mountpoint: Path, placements: Sequence[Mapping[str, object]],
) -> UsbDeviceScan | None:
    """Read and hash the *whole managed subtree* of an attested USB mount.

    Every physically observed file appears either under the unique managed
    artifact ID from an exact stored path, or under an opaque `untracked:`
    key. Thus a file at a planned target cannot be hidden as 'missing'.
    The result is a preview snapshot, not a capability for transfer/remove.

    The mounted volume must be re-attested before and after the walk.
    Missing managed directory, unusual inode, collision, symlink, mount
    replacement, read failure and bounds exhaustion all fail closed.
    """
    if (type(identity_id) is not str or not identity_id
            or not isinstance(binding, Mapping)
            or type(binding.get("device_id")) is not str
            or binding.get("identity_id") != identity_id
            or binding.get("enabled") is not True
            or not isinstance(mountpoint, Path)):
        return None
    managed = binding.get("target_directory")
    if type(managed) is not str or not _relative(managed):
        return None
    target_map = _managed_target_map(
        identity_id=identity_id, device_id=binding["device_id"],
        managed=managed, placements=placements,
    )
    if target_map is None or not hasattr(os, "O_NOFOLLOW"):
        return None
    before = observe_linux_usb_mount(
        identity_id=identity_id, binding=dict(binding),
        mountpoint=mountpoint,
    )
    if before is None or before.mountpoint != mountpoint:
        return None
    expected_dev = os.makedev(before.major, before.minor)
    directory_flag = (
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )
    descriptors: list[int] = []
    try:
        root_fd = os.open(mountpoint, directory_flag)
        descriptors.append(root_fd)
        root_stat = os.fstat(root_fd)
        if (not stat.S_ISDIR(root_stat.st_mode)
                or root_stat.st_dev != expected_dev
                or linux_mount._fd_mount_id(root_fd) != before.mount_id):
            return None
        current_fd = root_fd
        for component in managed.split("/"):
            next_fd = os.open(component, directory_flag, dir_fd=current_fd)
            descriptors.append(next_fd)
            opened = os.fstat(next_fd)
            if (not stat.S_ISDIR(opened.st_mode) or opened.st_dev != expected_dev
                    or linux_mount._fd_mount_id(next_fd) != before.mount_id):
                return None
            current_fd = next_fd
        found: dict[str, UsbObservedFile] = {}
        unique_paths: set[str] = set()
        counters = {"files": 0, "dirs": 0, "bytes": 0}

        def walk(fd: int, prefix: str, depth: int) -> bool:
            if depth > _MAX_DEPTH or counters["dirs"] >= _MAX_DIRS:
                return False
            counters["dirs"] += 1
            start = os.fstat(fd)
            if not stat.S_ISDIR(start.st_mode) or start.st_dev != expected_dev:
                return False
            # A single adversarial/full USB directory may have millions of
            # entries. Never materialize its entire iterator before enforcing
            # a bound. Even directories and filenames without valid book
            # extensions count towards the cap.
            limit = _MAX_FILES + _MAX_DIRS
            with os.scandir(fd) as listing:
                entries = list(islice(listing, limit + 1))
            if len(entries) > limit:
                return False
            for entry in entries:
                name = entry.name
                if (not _relative(name) or "/" in name
                        or not isinstance(name, str)):
                    return False
                rel = prefix + "/" + name
                if not _relative(rel):
                    return False
                path_key = rel.casefold()
                if path_key in unique_paths:
                    return False
                unique_paths.add(path_key)
                original = entry.stat(follow_symlinks=False)
                if original.st_dev != expected_dev:
                    return False
                if stat.S_ISDIR(original.st_mode):
                    childfd = os.open(name, directory_flag, dir_fd=fd)
                    try:
                        current = os.fstat(childfd)
                        if ((current.st_dev, current.st_ino)
                                != (original.st_dev, original.st_ino)
                                or linux_mount._fd_mount_id(childfd) != before.mount_id
                                or not walk(childfd, rel, depth + 1)):
                            return False
                    finally:
                        os.close(childfd)
                elif stat.S_ISREG(original.st_mode):
                    if counters["files"] >= _MAX_FILES:
                        return False
                    actual = _regular_hash(
                        fd, name, original,
                        budget=_MAX_TOTAL_BYTES - counters["bytes"],
                        mount_id=before.mount_id,
                    )
                    if actual is None:
                        return False
                    digest, size = actual
                    counters["files"] += 1
                    counters["bytes"] += size
                    artifact = target_map.get(path_key)
                    key = artifact or "untracked:" + sha256(rel.encode("utf-8")).hexdigest()
                    if key in found:
                        return False
                    found[key] = UsbObservedFile(
                        relative_path=rel, sha256=digest, regular_file=True,
                    )
                else:
                    # Never follow symlinks or treat special files as books.
                    return False
            end = os.fstat(fd)
            return (
                start.st_dev == end.st_dev
                and start.st_ino == end.st_ino
                and start.st_mtime_ns == end.st_mtime_ns
                and start.st_ctime_ns == end.st_ctime_ns
            )

        if not walk(current_fd, managed, 0):
            return None
        after = observe_linux_usb_mount(
            identity_id=identity_id, binding=dict(binding),
            mountpoint=mountpoint,
        )
        if (after is None or after.scope_key() != before.scope_key()
                or not linux_mount._directory_chain_current(
                    mountpoint, managed, descriptors, before.mount_id,
                )):
            return None
        return UsbDeviceScan(
            identity_id=identity_id, device_id=binding["device_id"],
            fingerprint=before.usb_fingerprint, observed_at=int(time.time()),
            complete=True, files=found,
        )
    except (OSError, ValueError, UnicodeError, OverflowError):
        return None
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


__all__ = ["scan_managed_usb"]
