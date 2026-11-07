"""P1 #2854: physical USB target/space preflight, strictly read-only.

Preflight is advisory. Even a successful observation never grants transfer
rights, user confirmation, a pinned mount or a future write capability.
The eventual executor must recheck every fact under its own exclusive lock.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
import os
from pathlib import Path
import stat
from typing import Mapping

from . import device_usb_mount as linux_mount
from .device_usb_mount import LinuxUsbMount, observe_linux_usb_mount
from .device_usb_preview import _relative, _sha
from .device_usb_variant_review import _SUFFIX


_MAX_ENTRIES = 10000
_MAX_FILE_BYTES = 4 * 1024**3
_SPACE_RESERVE = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class UsbTargetPreflight:
    status: str
    reason: str
    available_bytes: int | None = None
    # Never a permission or a reservation; it can become stale immediately.
    executable: bool = False


def _blocked(reason: str) -> UsbTargetPreflight:
    return UsbTargetPreflight("blocked", reason)


def _target_aliases(directory_fd: int, name: str) -> tuple[str, ...] | None:
    """Return case-fold aliases or None when the directory cannot be bounded."""
    with os.scandir(directory_fd) as listing:
        entries = list(islice(listing, _MAX_ENTRIES + 1))
    if len(entries) > _MAX_ENTRIES:
        return None
    folded = name.casefold()
    return tuple(entry.name for entry in entries if entry.name.casefold() == folded)


def preflight_usb_target(
    *, identity_id: str, binding: Mapping[str, object],
    mountpoint: Path, artifact: Mapping[str, object],
    intention: Mapping[str, object],
) -> UsbTargetPreflight:
    """Examine one SHA-named target on the exact active Linux USB volume.

    Only read-only kernel observations, directory file descriptors, and free
    space are consulted. No mkdir, file create, delete, renaming, mounting,
    entitlements, SQLite updates or USB file content reads are performed.
    """
    if (type(identity_id) is not str or not identity_id
            or type(binding) is not dict
            or binding.get("identity_id") != identity_id
            or type(binding.get("device_id")) is not str
            or not binding["device_id"]
            or binding.get("enabled") is not True
            or not isinstance(mountpoint, Path)
            or not mountpoint.is_absolute() or ".." in mountpoint.parts):
        return _blocked("target_scope_unverified")
    metadata = binding.get("metadata")
    fingerprint = metadata.get("usb_fingerprint") if type(metadata) is dict else None
    if (type(fingerprint) is not str or not 8 <= len(fingerprint) <= 128
            or any(ch not in "abcdefghijklmnopqrstuvwxyz"
                   "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
                   for ch in fingerprint)):
        return _blocked("target_scope_unverified")
    managed = binding.get("target_directory")
    if type(managed) is not str or not _relative(managed):
        return _blocked("target_directory_unverified")
    digest = artifact.get("sha256") if isinstance(artifact, Mapping) else None
    artifact_id = artifact.get("artifact_id") if isinstance(artifact, Mapping) else None
    size = artifact.get("size") if isinstance(artifact, Mapping) else None
    fmt = artifact.get("media_format") if isinstance(artifact, Mapping) else None
    if (not isinstance(artifact, Mapping)
            or type(digest) is not str or not _sha(digest)
            or type(artifact_id) is not str or not artifact_id
            or (artifact_id.startswith("sha256:")
                and artifact_id != "sha256:" + digest)
            or type(size) is not int or not 0 < size <= _MAX_FILE_BYTES
            or type(fmt) is not str or fmt not in _SUFFIX
            or artifact.get("kind") != "file"):
        return _blocked("target_artifact_unverified")
    expected_path = managed + "/" + digest + _SUFFIX[fmt]
    if (not _relative(expected_path) or not isinstance(intention, Mapping)
            or intention.get("device_id") != binding["device_id"]
            or intention.get("artifact_id") != artifact_id
            or intention.get("desired_state") != "keep"
            or type(intention.get("metadata")) is not dict
            or intention["metadata"].get("intent_identity_id") != identity_id
            or intention.get("target_relative_path") != expected_path):
        return _blocked("target_intention_unverified")

    # All three are mandatory: an actual UUID block device, opened pinned
    # directories on that filesystem, and a second independent mount check.
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        return _blocked("target_platform_unsupported")
    first = observe_linux_usb_mount(
        identity_id=identity_id, binding=binding, mountpoint=mountpoint,
    )
    if (type(first) is not LinuxUsbMount
            or first.mountpoint != mountpoint
            or first.identity_id != identity_id
            or first.device_id != binding["device_id"]
            or first.usb_fingerprint != fingerprint):
        return _blocked("target_mount_unverified")
    expected_device = os.makedev(first.major, first.minor)
    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    fds: list[int] = []
    try:
        current = os.open(mountpoint, directory_flags)
        fds.append(current)
        root = os.fstat(current)
        if (not stat.S_ISDIR(root.st_mode) or root.st_dev != expected_device
                or linux_mount._fd_mount_id(current) != first.mount_id):
            return _blocked("target_mount_unverified")
        for component in managed.split("/"):
            child = os.open(component, directory_flags, dir_fd=current)
            fds.append(child)
            current = child
            st = os.fstat(current)
            if (not stat.S_ISDIR(st.st_mode) or st.st_dev != expected_device
                    or linux_mount._fd_mount_id(child) != first.mount_id):
                return _blocked("target_directory_unverified")
        before = os.fstat(current)
        name = digest + _SUFFIX[fmt]
        aliases = _target_aliases(current, name)
        if aliases is None:
            return _blocked("target_directory_unbounded")
        if aliases:
            return _blocked("target_occupied")
        # The no-overwrite condition is corroborated via the directory FD.
        try:
            os.stat(name, dir_fd=current, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return _blocked("target_occupied")
        volume = os.fstatvfs(current)
        if volume.f_flag & getattr(os, "ST_RDONLY", 1):
            return _blocked("target_volume_read_only")
        available = volume.f_bavail * volume.f_frsize
        if (type(available) is not int or available < 0
                or available > 2**63 - 1):
            return _blocked("target_space_unverified")
        if size + _SPACE_RESERVE > available:
            return _blocked("target_space_insufficient")
        after = os.fstat(current)
        if (before.st_dev, before.st_ino, before.st_mtime_ns,
            before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_mtime_ns,
                after.st_ctime_ns):
            return _blocked("target_directory_changed")
        last = observe_linux_usb_mount(
            identity_id=identity_id, binding=binding, mountpoint=mountpoint,
        )
        if type(last) is not LinuxUsbMount or last.scope_key() != first.scope_key():
            return _blocked("target_mount_changed")
        if not linux_mount._directory_chain_current(
            mountpoint, managed, fds, first.mount_id,
        ):
            return _blocked("target_directory_changed")
        return UsbTargetPreflight(
            "review", "target_absent_space_observed", available,
        )
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
        return _blocked("target_io_unverified")
    finally:
        for fd in reversed(fds):
            os.close(fd)


__all__ = ["UsbTargetPreflight", "preflight_usb_target"]
