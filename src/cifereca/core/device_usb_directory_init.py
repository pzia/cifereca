"""Explicit managed-directory initialization; never a preview side effect."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from . import device_usb_mount as mount_ops
from .device_usb_mount import LinuxUsbMount
from .device_usb_preview import _relative
from .device_usb_target_preflight import _target_aliases


def initialize_managed_reader_directory(
    *, mount: LinuxUsbMount, managed: str, current: Callable[[], bool],
) -> bool:
    """Create missing directory components only, under a trusted Core lease.

    The caller owns the global synchronization lock and reattests destination
    and binding in current(). Existing directories are never modified or
    adopted as managed books. No symlinks, mount crossings, aliases or unlink.
    """
    if (type(mount) is not LinuxUsbMount or not _relative(managed)
            or not callable(current) or not isinstance(mount.mountpoint, Path)
            or not mount.mountpoint.is_absolute() or mount.mountpoint == Path("/")):
        return False
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fds = []
    try:
        if current() is not True:
            return False
        root_fd = os.open(mount.mountpoint, flags)
        fds.append(root_fd)
        device = os.makedev(mount.major, mount.minor)
        if (os.fstat(root_fd).st_dev != device
                or mount_ops._fd_mount_id(root_fd) != mount.mount_id):
            return False
        components = managed.split("/")
        for i, component in enumerate(components):
            parent = fds[-1]
            if (current() is not True
                    or (i and not mount_ops._directory_chain_current(
                        mount.mountpoint, "/".join(components[:i]), fds, mount.mount_id))
                    or (not i and (os.fstat(parent).st_dev, os.fstat(parent).st_ino)
                        != (mount.mountpoint.lstat().st_dev, mount.mountpoint.lstat().st_ino))):
                return False
            aliases = _target_aliases(parent, component)
            if aliases == ():
                try:
                    os.mkdir(component, 0o700, dir_fd=parent)
                    os.fsync(parent)
                except FileExistsError:
                    # A concurrent creator must still pass the exact alias
                    # and no-follow checks below.
                    pass
            elif aliases != (component,):
                return False
            if _target_aliases(parent, component) != (component,):
                return False
            child = os.open(component, flags, dir_fd=parent)
            fds.append(child)
            if (os.fstat(child).st_dev != device
                    or mount_ops._fd_mount_id(child) != mount.mount_id):
                return False
        return (current() is True and mount_ops._directory_chain_current(
            mount.mountpoint, managed, fds, mount.mount_id))
    except (OSError, ValueError, TypeError, RuntimeError):
        return False
    finally:
        for fd in reversed(fds):
            os.close(fd)
