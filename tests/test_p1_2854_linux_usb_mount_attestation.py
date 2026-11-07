"""P1 #2854: USB mount evidence must come from Linux, not configured labels.

All cases are synthetic or use a temporary ordinary directory; no real
reader, removable volume, loan, network or privileged command is accessed.
"""
from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import stat
from types import SimpleNamespace as NS

import pytest

from cifereca.core.device_usb_mount import (
    _decode_mountinfo_path, _fd_mount_id, _mount_record, _qualify_mount,
    _sysfs_is_usb, observe_linux_usb_mount,
)


MOUNT = "/media/alice/Kobo Reader"
ENCODED = "/media/alice/Kobo\\040Reader"
MAJOR, MINOR = 8, 17
USB_SYSFS = "/sys/devices/pci0000:00/0000:00:14.0/usb1/1-1/host6/block/sdb/sdb1"
INTERNAL_SYSFS = "/sys/devices/pci0000:00/0000:00:17.0/ata1/block/sda/sda1"


def mount_record(*, point=ENCODED, mounted_root="/", fs="vfat", mount_id=189,
                 major=MAJOR, minor=MINOR):
    return (
        f"{mount_id} 1 {major}:{minor} {mounted_root} {point} rw,nosuid,nodev - "
        f"{fs} /dev/sdb1 rw,uid=1000\n"
    )


def binding(*, owner="alice", device="reader-kobo", enabled=True,
            uuid="ABCD-1234"):
    return {
        "identity_id": owner, "device_id": device, "enabled": enabled,
        "metadata": {"usb_fingerprint": uuid},
    }


def test_linux_mountinfo_exact_mountpoint_and_octal_escapes():
    assert _decode_mountinfo_path(ENCODED) == MOUNT
    assert _decode_mountinfo_path("/media/x\\011tab") == "/media/x\ttab"
    assert _decode_mountinfo_path("/media/x\\134backslash") == "/media/x\\backslash"
    assert _decode_mountinfo_path("/media/x\\bad") is None
    assert _mount_record(mount_record(), MOUNT, MAJOR, MINOR) == ("vfat", 189)
    assert _mount_record(mount_record(), MOUNT + "2", MAJOR, MINOR) is None


@pytest.mark.parametrize("invalid", [
    mount_record(mounted_root="/Books"),
    mount_record(fs="fuse.kio-fuse"),
    mount_record(fs="overlay"),
    mount_record(mount_id="invalid"),
    mount_record(major=8, minor=18),
    mount_record(point="/media/alice/Other"),
    mount_record() * 2,
    "incomplete not mountinfo",
])
def test_malformed_bind_virtual_or_duplicate_mount_is_unknown(invalid):
    assert _mount_record(invalid, MOUNT, MAJOR, MINOR) is None


def test_only_usb_ancestor_proves_usb_block_device():
    assert _sysfs_is_usb(USB_SYSFS) is True
    assert _sysfs_is_usb(INTERNAL_SYSFS) is False
    assert _sysfs_is_usb("/sys/devices/pci/usb-not-real/block") is False
    assert _sysfs_is_usb("/tmp/fake/sys/devices/usb1/block") is False
    assert _sysfs_is_usb(None) is False


def test_pure_mount_qualification_validates_all_independent_kernel_facts():
    facts = dict(
        mountpoint=MOUNT, mountinfo=mount_record(),
        root_device=os.makedev(MAJOR, MINOR),
        uuid_device=os.makedev(MAJOR, MINOR),
        uuid_mode=stat.S_IFBLK | 0o660, uuid_is_symlink=True,
        sysfs_is_symlink=True, sysfs_target=USB_SYSFS,
    )
    assert _qualify_mount(**facts) == ("vfat", MAJOR, MINOR, 189)
    for bad in (
        {"root_device": os.makedev(8, 19)},
        {"uuid_device": os.makedev(8, 18)},
        {"uuid_mode": stat.S_IFREG | 0o600},
        {"uuid_is_symlink": False},
        {"sysfs_is_symlink": False},
        {"sysfs_target": INTERNAL_SYSFS},
        {"mountinfo": mount_record(mounted_root="/subdir")},
        {"mountinfo": mount_record(fs="fuseblk")},
    ):
        assert _qualify_mount(**(facts | bad)) is None


def test_real_ordinary_directory_cannot_be_attested_as_usb(tmp_path):
    mount = tmp_path / "not-usb"
    mount.mkdir()
    assert observe_linux_usb_mount(
        identity_id="alice", binding=binding(), mountpoint=mount,
    ) is None
    assert observe_linux_usb_mount(
        identity_id="bob", binding=binding(), mountpoint=mount,
    ) is None


@pytest.mark.parametrize("changes", [
    {"identity_id": "bob"},
    {"mountpoint": Path("/tmp/../tmp")},
    {"mountpoint": Path("relative")},
    {"binding": binding(enabled=False)},
    {"binding": binding(enabled="true")},
    {"binding": binding(uuid="../../../proc/")},
    {"binding": binding(uuid="short")},
    {"binding": binding(owner="bob")},
    {"binding": binding(device="")},
])
def test_no_unproven_or_invalid_configuration_can_attest_mount(tmp_path, changes):
    values = dict(
        identity_id="alice", binding=binding(), mountpoint=tmp_path,
    )
    values.update(changes)
    assert observe_linux_usb_mount(**values) is None


def test_production_reader_checks_every_kernel_source_before_returning_evidence(monkeypatch):
    """Model a Linux USB block volume without any real block-device access."""
    mount = Path(MOUNT)
    uuid_node = Path("/dev/disk/by-uuid/ABCD-1234")
    sysfs_node = Path(f"/sys/dev/block/{MAJOR}:{MINOR}")
    rootstat = NS(st_mode=stat.S_IFDIR | 0o755,
                  st_dev=os.makedev(MAJOR, MINOR), st_ino=41)
    diskstat = NS(st_mode=stat.S_IFBLK | 0o660,
                  st_rdev=os.makedev(MAJOR, MINOR))

    def resolve(self, *, strict=False):
        if self == mount:
            return mount
        if self == sysfs_node:
            return Path(USB_SYSFS)
        raise FileNotFoundError(self)

    def lstat(self):
        if self == mount:
            return rootstat
        raise FileNotFoundError(self)

    def get_stat(self):
        if self == mount:
            return rootstat
        if self == uuid_node:
            return diskstat
        raise FileNotFoundError(self)

    def is_symlink(self):
        return self in (uuid_node, sysfs_node)

    def open_mock(self, *args, **kwargs):
        if self == Path("/proc/self/mountinfo"):
            return StringIO(mount_record())
        raise FileNotFoundError(self)

    # Deliberately scoped: the production function has no option to inject
    # fake 'mounted=True' or bypass the host /proc, /sys and udev checks.
    with monkeypatch.context() as patched:
        patched.setattr(Path, "resolve", resolve)
        patched.setattr(Path, "lstat", lstat)
        patched.setattr(Path, "stat", get_stat)
        patched.setattr(Path, "is_symlink", is_symlink)
        patched.setattr(Path, "open", open_mock)
        got = observe_linux_usb_mount(
            identity_id="alice", binding=binding(), mountpoint=mount,
        )
    assert got is not None
    assert got.device_id == "reader-kobo"
    assert got.identity_id == "alice"
    assert got.usb_fingerprint == "ABCD-1234"
    assert got.filesystem == "vfat"
    assert (got.major, got.minor) == (MAJOR, MINOR)
    assert got.mount_id == 189
    assert got.mountpoint == mount
    assert type(got.checked_at) is int and got.checked_at > 0
    # Only a mounting fact was observed. No files or rights are inferred.
    assert not hasattr(got, "files")
    assert not hasattr(got, "complete")


@pytest.mark.parametrize("fdinfo,expected", [
    ("pos:\t0\nflags:\t02300000\nmnt_id:\t189\nino:\t42\n", 189),
    ("pos:\t0\nino:\t42\n", None),
    ("mnt_id:\t0\n", None),
    ("mnt_id:\t189\nmnt_id:\t190\n", None),
    ("mnt_id:\t189\n" + "x" * 4096, None),
])
def test_kernel_fd_mount_id_must_be_unique_and_bounded(monkeypatch, fdinfo, expected):
    def read_fdinfo(self, *, encoding):
        assert self == Path("/proc/self/fdinfo/123")
        assert encoding == "ascii"
        return StringIO(fdinfo)

    monkeypatch.setattr(Path, "open", read_fdinfo)
    assert _fd_mount_id(123) == expected
