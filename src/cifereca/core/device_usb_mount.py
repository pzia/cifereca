"""P1 #2854: fail-closed Linux USB block-volume *mount* attestation.

This is a read-only preflight for the later managed-file observer. Declaring
`usb_fingerprint` in SQLite, knowing a directory name or seeing a mount is
never enough to mark a book present. No UsbDeviceScan is produced here.
A future file observer/executor must recheck identity, mount, inode and SHA.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import time
from typing import Mapping


_MAX_FDINFO = 4096
_MAX_MOUNTINFO = 1024 * 1024
_UUID = re.compile(r"[A-Za-z0-9._:-]{8,128}\Z", re.ASCII)
_OCTAL = re.compile(r"\\([0-7]{3})")
_FS_TYPES = frozenset({"vfat", "exfat", "ntfs3", "ext4", "f2fs", "xfs"})


@dataclass(frozen=True, slots=True)
class LinuxUsbMount:
    """Evidence for an exact active mounted USB *block volume*, not a file."""

    identity_id: str
    device_id: str
    usb_fingerprint: str
    mountpoint: Path
    filesystem: str
    major: int
    minor: int
    checked_at: int
    mount_id: int

    def scope_key(self) -> tuple[object, ...]:
        """Identify the mount generation independently of observation time."""
        return (
            self.identity_id, self.device_id, self.usb_fingerprint,
            self.mountpoint, self.filesystem, self.major, self.minor,
            self.mount_id,
        )


def _mount_id(value: str) -> int | None:
    if re.fullmatch(r"[1-9][0-9]{0,19}", value):
        return int(value)
    return None


def _fd_mount_id(fd: int) -> int | None:
    """Get the mount of an open Linux descriptor, including bind mounts."""
    with Path(f"/proc/self/fdinfo/{fd}").open(encoding="ascii") as handle:
        data = handle.read(_MAX_FDINFO + 1)
    if len(data) > _MAX_FDINFO:
        return None
    matches = [
        line.partition(":")[2].strip() for line in data.splitlines()
        if line.startswith("mnt_id:")
    ]
    return _mount_id(matches[0]) if len(matches) == 1 else None


def _directory_chain_current(
    mountpoint: Path, managed: str, descriptors: list[int], mount_id: int,
) -> bool:
    """Check that pinned directory inodes still occupy the configured path."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    parent_fd = None
    for name, fd in zip((mountpoint, *managed.split("/")), descriptors, strict=True):
        live_fd = os.open(name, flags, dir_fd=parent_fd)
        try:
            current = os.fstat(live_fd)
            opened = os.fstat(fd)
            if ((current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                    or _fd_mount_id(live_fd) != mount_id):
                return False
        finally:
            os.close(live_fd)
        parent_fd = fd
    return True


def _decode_mountinfo_path(raw: str) -> str | None:
    """Decode only the octal escapes emitted by the Linux mountinfo ABI."""
    if not raw or "\\" in _OCTAL.sub("", raw):
        return None
    try:
        return _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), raw)
    except ValueError:
        return None


def _mount_record(
    text: str, path: str, major: int, minor: int,
) -> tuple[str, int] | None:
    """Find exactly one direct mount of a supported block filesystem.

    A bind mount or a nested filesystem root is not evidence that a path
    identifies the reader's entire volume. A malformed or duplicate matching
    entry makes the result unknown rather than asserting that it is present.
    """
    if type(text) is not str or len(text) > _MAX_MOUNTINFO:
        return None
    matches = []
    for line in text.splitlines():
        left_sep = line.split(" - ", 1)
        if len(left_sep) != 2:
            continue
        left, right = left_sep[0].split(), left_sep[1].split()
        if len(left) < 6 or len(right) < 3:
            continue
        decoded = _decode_mountinfo_path(left[4])
        if decoded != path:
            continue
        matches.append((left, right))
    if len(matches) != 1:
        return None
    left, right = matches[0]
    mount_id = _mount_id(left[0])
    if (mount_id is None or left[2] != f"{major}:{minor}"
            or left[3] != "/"
            or right[0] not in _FS_TYPES):
        return None
    return right[0], mount_id


def _sysfs_is_usb(path: str) -> bool:
    """The block device must descend from a Linux USB bus, not a disk label."""
    if type(path) is not str or not path.startswith("/sys/devices/"):
        return False
    parts = Path(path).parts
    return any(re.fullmatch(r"usb[0-9]+", part) for part in parts)


def _qualify_mount(
    *, mountpoint: str, mountinfo: str, root_device: int,
    uuid_device: int, uuid_mode: int, uuid_is_symlink: bool,
    sysfs_is_symlink: bool, sysfs_target: str,
) -> tuple[str, int, int, int] | None:
    """Pure validation of data supplied by trusted Linux kernel interfaces."""
    if not (type(root_device) is int and type(uuid_device) is int
            and type(uuid_mode) is int
            and uuid_is_symlink is True and sysfs_is_symlink is True
            and stat.S_ISBLK(uuid_mode) and root_device == uuid_device
            and _sysfs_is_usb(sysfs_target)):
        return None
    major, minor = os.major(root_device), os.minor(root_device)
    record = _mount_record(mountinfo, mountpoint, major, minor)
    if record is None:
        return None
    filesystem, mount_id = record
    return filesystem, major, minor, mount_id


def observe_linux_usb_mount(
    *, identity_id: str, binding: Mapping[str, object],
    mountpoint: Path,
) -> LinuxUsbMount | None:
    """Read kernel mount/UUID/sysfs facts; return None on any ambiguity.

    Only the exact UUID stored in the matching selected profile's binding
    is accepted. No caller-supplied `attested=True` flag can bypass Linux
    mountinfo, the real block-device UUID, or its USB sysfs ancestry.

    Deliberately unsupported: MTP, FUSE, bind mounts, absent UUID symlink,
    internal disks, emulated folders, and volumes behind USB abstractions
    without an independently verifiable USB block-device lineage.
    """
    if (type(identity_id) is not str or not identity_id
            or type(binding) is not dict
            or binding.get("identity_id") != identity_id
            or type(binding.get("device_id")) is not str
            or not binding["device_id"]
            or binding.get("enabled") is not True
            or not isinstance(mountpoint, Path)
            or not mountpoint.is_absolute()
            or ".." in mountpoint.parts):
        return None
    metadata = binding.get("metadata")
    if type(metadata) is not dict:
        return None
    fingerprint = metadata.get("usb_fingerprint")
    if type(fingerprint) is not str or _UUID.fullmatch(fingerprint) is None:
        return None
    try:
        # A user-controlled symlink (or an aliasing ancestor) must not change
        # the inspected root after obtaining a binding for a different path.
        if mountpoint.resolve(strict=True) != mountpoint:
            return None
        root_lstat = mountpoint.lstat()
        if not stat.S_ISDIR(root_lstat.st_mode):
            return None
        mount_st = mountpoint.stat()
        if not stat.S_ISDIR(mount_st.st_mode):
            return None

        uuid_path = Path("/dev/disk/by-uuid") / fingerprint
        if not uuid_path.is_symlink():
            return None
        disk_st = uuid_path.stat()
        if not stat.S_ISBLK(disk_st.st_mode):
            return None

        major, minor = os.major(mount_st.st_dev), os.minor(mount_st.st_dev)
        sysfs_path = Path(f"/sys/dev/block/{major}:{minor}")
        if not sysfs_path.is_symlink():
            return None
        sysfs_target = str(sysfs_path.resolve(strict=True))

        # Proc files have st_size=0; bound the actual read, not stat().st_size.
        with Path("/proc/self/mountinfo").open(encoding="utf-8") as handle:
            mountinfo = handle.read(_MAX_MOUNTINFO + 1)

        proof = _qualify_mount(
            mountpoint=str(mountpoint), mountinfo=mountinfo,
            root_device=mount_st.st_dev, uuid_device=disk_st.st_rdev,
            uuid_mode=disk_st.st_mode,
            uuid_is_symlink=True, sysfs_is_symlink=True,
            sysfs_target=sysfs_target,
        )
        if proof is None:
            return None
        # The inspected volume must not have been replaced while reading the
        # kernel-provided evidence. This is only a preliminary observation;
        # the future copy/removal executor must repeat it after locking.
        after = mountpoint.stat()
        if (after.st_dev != mount_st.st_dev
                or after.st_ino != mount_st.st_ino
                or not stat.S_ISDIR(after.st_mode)):
            return None
        filesystem, major, minor, mount_id = proof
        return LinuxUsbMount(
            identity_id, binding["device_id"], fingerprint,
            mountpoint, filesystem, major, minor, int(time.time()), mount_id,
        )
    except (OSError, ValueError, UnicodeError, OverflowError):
        return None


__all__ = ["LinuxUsbMount", "observe_linux_usb_mount"]
