"""#6041: discover only Linux USB block volumes, never storage contents.

Synthetic kernel interfaces. No mounted test disk or privileged operation.
"""
from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import stat
from types import SimpleNamespace as NS

import pytest

from cifereca.core import device_usb_discovery as discovery


USB_SYSFS = "/sys/devices/pci0000:00/0000:00:14.0/usb1/1-1/host6/block/sdb/sdb1"
INTERNAL_SYSFS = "/sys/devices/pci0000:00/0000:00:17.0/ata1/block/sda/sda1"
MOUNT = Path("/media/alice/SanDisk")
UUID = "ABCD-1234"


def entry(point=MOUNT, *, mid=189, dev="8:17", root="/",
          fs="exfat", source="/dev/sdb1"):
    path = str(point).replace(" ", r"\040")
    return (f"{mid} 1 {dev} {root} {path} rw,nosuid,nodev"
            f" - {fs} {source} rw\n")


def kernel(monkeypatch, lines, *, sysfs=USB_SYSFS,
           uuids={(8, 17): UUID}, size=500_000_000_000):
    """Patch only read-only kernel surfaces used by discovery."""
    monkeypatch.setattr(discovery, "_uuid_devices", lambda: dict(uuids))
    def resolve(self, *, strict=False):
        if self == MOUNT:
            return self
        if str(self) == "/sys/dev/block/8:17":
            return Path(sysfs)
        if str(self) == "/sys/dev/block/8:18":
            return Path(sysfs)
        if str(self) == "/sys/dev/block/259:1":
            return Path(INTERNAL_SYSFS)
        return self
    def stat_path(self):
        if self == MOUNT:
            return NS(st_mode=stat.S_IFDIR | 0o755,
                      st_dev=os.makedev(8, 17), st_ino=43)
        raise OSError("missing physical volume")
    def symlink(self):
        return str(self) in (
            "/sys/dev/block/8:17", "/sys/dev/block/8:18",
            "/sys/dev/block/259:1",
        )
    def open_mountinfo(self, *, encoding):
        assert self == Path("/proc/self/mountinfo")
        assert encoding == "utf-8"
        return StringIO(lines)
    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "stat", stat_path)
    monkeypatch.setattr(Path, "lstat", stat_path)
    monkeypatch.setattr(Path, "is_symlink", symlink)
    monkeypatch.setattr(Path, "open", open_mountinfo)
    monkeypatch.setattr(
        discovery.os, "statvfs", lambda p: NS(
            f_frsize=4096, f_blocks=size // 4096,
        )
    )


def test_sandisk_exfat_is_visible_with_capacity_and_unexposed_uuid(monkeypatch):
    with monkeypatch.context() as ctx:
        kernel(ctx, entry())
        rows = discovery.discover_linux_usb_volumes()
    assert len(rows) == 1
    volume, = rows
    assert volume.usb_fingerprint == UUID
    assert volume.mountpoint == MOUNT
    assert volume.filesystem == "exfat"
    assert volume.label == "SanDisk"
    assert volume.capacity_bytes is not None
    assert volume.mount_id == 189
    assert "SanDisk" in volume.display_label()
    assert "exFAT" in volume.display_label()
    assert "Go" in volume.display_label()
    assert UUID not in volume.display_label()
    assert volume.selection_key()[-1] == 189


@pytest.mark.parametrize("disqualified", [
    entry(fs="fuseblk"),
    entry(fs="overlay"),
    entry(fs="vfat", root="/Books"),
    entry(dev="8:18"),
    entry(source="none"),
    entry() + entry(point="/media/alice/Another", mid=190),
])
def test_unqualified_or_ambiguous_mount_never_shows_as_choice(
    monkeypatch, disqualified,
):
    with monkeypatch.context() as ctx:
        kernel(ctx, disqualified)
        assert discovery.discover_linux_usb_volumes() == ()


def test_internal_disk_and_unmounted_uuid_cannot_be_offered(monkeypatch):
    with monkeypatch.context() as ctx:
        kernel(ctx, entry(), sysfs=INTERNAL_SYSFS)
        assert discovery.discover_linux_usb_volumes() == ()
    with monkeypatch.context() as ctx:
        kernel(ctx, entry(), uuids={})
        assert discovery.discover_linux_usb_volumes() == ()
    with monkeypatch.context() as ctx:
        kernel(ctx, entry(point="/mnt/internal", dev="259:1", source="/dev/nvme0n1p1"),
               uuids={(259, 1): "INTERNAL-0001"})
        assert discovery.discover_linux_usb_volumes() == ()


def test_malformed_or_excessive_mountinfo_returns_no_volume():
    assert discovery._mount_rows("invalid mountinfo\n") == ()
    assert discovery._mount_rows("x" * (1024 * 1024 + 1)) == ()
    assert discovery._mount_rows(entry()) != ()


def test_duplicate_udev_uuid_aliases_are_omitted(monkeypatch):
    base = Path("/dev/disk/by-uuid")
    nodes = (base / "ABCD-1234", base / "ABCD-1235")
    def iterdir(self):
        assert self == base
        return iter(nodes)
    def symlink(self):
        return self in nodes
    def stat_path(self):
        assert self in nodes
        return NS(st_mode=stat.S_IFBLK, st_rdev=os.makedev(8, 17))
    with monkeypatch.context() as ctx:
        ctx.setattr(Path, "iterdir", iterdir)
        ctx.setattr(Path, "is_symlink", symlink)
        ctx.setattr(Path, "stat", stat_path)
        assert discovery._uuid_devices() == {}


def test_udev_device_to_uuid_mapping_must_be_unique_and_block(monkeypatch):
    base = Path("/dev/disk/by-uuid")
    node = base / UUID
    with monkeypatch.context() as ctx:
        ctx.setattr(Path, "iterdir", lambda self: iter((node,)))
        ctx.setattr(Path, "is_symlink", lambda self: self == node)
        ctx.setattr(Path, "stat", lambda self: NS(
            st_mode=stat.S_IFBLK | 0o660, st_rdev=os.makedev(8, 17),
        ))
        assert discovery._uuid_devices() == {(8, 17): UUID}
