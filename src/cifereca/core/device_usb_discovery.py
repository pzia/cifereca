"""#6041: discover *mounted* Linux USB block volumes for the pairing chooser.

Read-only presentation, not USB delivery admission. Pairing still stores only
an exact declarative UUID; every subsequent scanner/transfer must independently
attest the current kernel mount and device identity under its own lock.
Never inspect a volume's contents, mount it, write to it or run subprocesses.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import islice
import os
from pathlib import Path
import re
import stat

from .device_usb_mount import (
    _MAX_MOUNTINFO, _UUID, _FS_TYPES, _decode_mountinfo_path,
    _mount_id, _qualify_mount,
)


_MAX_VOLUME_ROWS = 128
_MAX_UUID_ENTRIES = 1024
_DEVICE_PAIR = re.compile(r"([0-9]{1,5}):([0-9]{1,7})\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class MountedUsbVolume:
    """A revocable chooser observation, not a transferable/usable device."""

    usb_fingerprint: str
    mountpoint: Path
    filesystem: str
    label: str
    capacity_bytes: int | None
    major: int
    minor: int
    mount_id: int

    def selection_key(self) -> tuple[object, ...]:
        return (
            self.usb_fingerprint, self.mountpoint, self.filesystem,
            self.major, self.minor, self.mount_id,
        )

    def display_label(self) -> str:
        capacity = (
            f"{round(self.capacity_bytes / (1024 ** 3))} Go"
            if type(self.capacity_bytes) is int else "taille inconnue"
        )
        filesystem = {"vfat": "FAT", "exfat": "exFAT"}.get(
            self.filesystem, self.filesystem.upper(),
        )
        return f"{self.label} · {capacity} · {filesystem} · {self.mountpoint}"


def _mount_rows(mountinfo: str):
    """Only a direct mount of the whole supported block filesystem is a choice."""
    if type(mountinfo) is not str or len(mountinfo) > _MAX_MOUNTINFO:
        return ()
    rows = []
    for line in mountinfo.splitlines():
        halves = line.split(" - ", 1)
        if len(halves) != 2:
            continue
        left, right = halves[0].split(), halves[1].split()
        if len(left) < 6 or len(right) < 3:
            continue
        pair = _DEVICE_PAIR.fullmatch(left[2])
        mount_id = _mount_id(left[0])
        point = _decode_mountinfo_path(left[4])
        if pair is None or mount_id is None or point is None:
            continue
        major, minor = int(pair[1]), int(pair[2])
        # Keep even unsupported/bind mounts for the duplicate-device check.
        rows.append((
            major, minor, mount_id, left[3], point, right[0], right[1],
        ))
    return tuple(rows) if len(rows) <= _MAX_VOLUME_ROWS * 8 else ()


def _uuid_devices() -> dict[tuple[int, int], str]:
    """Map Linux block dev_t to one *unique* udev UUID symlink.

    A duplicate UUID, an alias for one device or an excessive directory
    makes discovery unavailable; none of them is a safe implicit choice.
    """
    base = Path("/dev/disk/by-uuid")
    devices: dict[tuple[int, int], list[str]] = defaultdict(list)
    # Bound enumeration itself: list(base.iterdir()) can allocate arbitrarily
    # before we ever check the limit on an adversarial/large udev directory.
    entries = list(islice(base.iterdir(), _MAX_UUID_ENTRIES + 1))
    if len(entries) > _MAX_UUID_ENTRIES:
        return {}
    for path in entries:
        uuid = path.name
        if _UUID.fullmatch(uuid) is None or not path.is_symlink():
            continue
        try:
            info = path.stat()
            if not stat.S_ISBLK(info.st_mode):
                continue
            devices[(os.major(info.st_rdev), os.minor(info.st_rdev))].append(uuid)
        except (OSError, ValueError, OverflowError):
            continue
    all_uuids = Counter(
        uuid.casefold() for labels in devices.values() for uuid in labels
    )
    return {
        device: labels[0]
        for device, labels in devices.items()
        if len(labels) == 1 and all_uuids[labels[0].casefold()] == 1
    }


def _safe_volume_label(value: object) -> str:
    if (type(value) is str and 1 <= len(value) <= 80
            and value == value.strip() and value.isprintable()
            and not any(ord(c) == 127 for c in value)):
        return value
    return "Volume USB"


def discover_linux_usb_volumes() -> tuple[MountedUsbVolume, ...]:
    """Enumerate only proven USB block mounts via mountinfo, udev and sysfs.

    Display facts are revocable. This read-only enumeration cannot attest an
    actual reader, grant access, select an owner or authorize a file transfer.
    """
    try:
        with Path("/proc/self/mountinfo").open(encoding="utf-8") as handle:
            mountinfo = handle.read(_MAX_MOUNTINFO + 1)
        if len(mountinfo) > _MAX_MOUNTINFO:
            return ()
        rows = _mount_rows(mountinfo)
        if not rows:
            return ()
        uuid_map = _uuid_devices()
        counts = Counter((major, minor) for major, minor, *_ in rows)
        candidates: list[MountedUsbVolume] = []
        for major, minor, mount_id, root, point, filesystem, source in rows:
            try:
                if (counts[major, minor] != 1 or root != "/"
                        or filesystem not in _FS_TYPES
                        or not source.startswith("/dev/")):
                    continue
                uuid = uuid_map.get((major, minor))
                if uuid is None:
                    continue
                mount = Path(point)
                if (not mount.is_absolute() or ".." in mount.parts
                        or mount.resolve(strict=True) != mount):
                    continue
                root_lstat = mount.lstat()
                before = mount.stat()
                if not stat.S_ISDIR(root_lstat.st_mode) or not stat.S_ISDIR(before.st_mode):
                    continue
                sysfs = Path(f"/sys/dev/block/{major}:{minor}")
                if not sysfs.is_symlink():
                    continue
                proof = _qualify_mount(
                    mountpoint=str(mount), mountinfo=mountinfo,
                    root_device=before.st_dev,
                    uuid_device=os.makedev(major, minor),
                    uuid_mode=stat.S_IFBLK,
                    uuid_is_symlink=True,
                    sysfs_is_symlink=True,
                    sysfs_target=str(sysfs.resolve(strict=True)),
                )
                if proof != (filesystem, major, minor, mount_id):
                    continue
                after = mount.stat()
                if (after.st_dev != before.st_dev
                        or after.st_ino != before.st_ino
                        or not stat.S_ISDIR(after.st_mode)):
                    continue
                try:
                    fs = os.statvfs(mount)
                    capacity = fs.f_frsize * fs.f_blocks
                    if type(capacity) is not int or not 0 <= capacity <= 2 ** 64 - 1:
                        capacity = None
                except (OSError, ValueError, OverflowError):
                    capacity = None
                candidates.append(MountedUsbVolume(
                    usb_fingerprint=uuid, mountpoint=mount,
                    filesystem=filesystem, label=_safe_volume_label(mount.name),
                    capacity_bytes=capacity, major=major, minor=minor,
                    mount_id=mount_id,
                ))
            except (OSError, ValueError, UnicodeError, OverflowError):
                # One disappearing or unreadable mount must not conceal other
                # independent USB block volumes in the chooser.
                continue
        return tuple(sorted(
            candidates, key=lambda row: (row.label.casefold(), str(row.mountpoint)),
        ))
    except (OSError, ValueError, UnicodeError, OverflowError):
        return ()


__all__ = ["MountedUsbVolume", "discover_linux_usb_volumes"]
