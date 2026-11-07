"""Atomic publication of a journaled USB stage through pinned Linux FDs."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
from hashlib import sha256
import os
import re
import stat
import sys
from typing import Callable, Literal

from . import device_usb_mount as linux_mount
from .device_usb_preview import _sha
from .device_usb_target_preflight import _MAX_FILE_BYTES, _target_aliases
from .device_usb_variant_review import _SUFFIX


_STAGE_NAME = re.compile(r"\.cifereca-usb-[a-f0-9]{32}\.part", re.ASCII)
_READ_CHUNK = 1024 * 1024
_RENAME_NOREPLACE = 1
_UNSUPPORTED = {errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL, errno.EXDEV}


@dataclass(frozen=True, slots=True)
class UsbFilePublication:
    status: Literal["published", "blocked", "recovery_required"]
    reason: str
    errno: int | None = None
    detail: str | None = None


def _rename_exclusive(directory_fd: int, staged_name: str, target_name: str) -> None:
    """Publish with Linux's no-replace primitive, also on filesystems without links."""
    rename = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOSYS, os.strerror(errno.ENOSYS))
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(directory_fd, os.fsencode(staged_name), directory_fd,
              os.fsencode(target_name), _RENAME_NOREPLACE) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _file_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _file_version(info: os.stat_result) -> tuple[int, ...]:
    return (*_file_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _same_file_at(
    directory_fd: int, name: str, expected: os.stat_result, mount_id: int,
) -> bool:
    fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        return (
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and _file_version(info) == _file_version(expected)
            and linux_mount._fd_mount_id(fd) == mount_id
        )
    finally:
        os.close(fd)


def _verified_file(fd: int, digest: str, size: int) -> os.stat_result | None:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != size:
        return None
    calculated = sha256()
    offset = 0
    while offset < size:
        block = os.pread(fd, min(_READ_CHUNK, size - offset), offset)
        if not block:
            return None
        calculated.update(block)
        offset += len(block)
    after = os.fstat(fd)
    if calculated.hexdigest() != digest or _file_version(before) != _file_version(after):
        return None
    return after


def publish_usb_staged_file(
    *, directory_fd: int, staged_fd: int, mount_id: int,
    staged_name: str, target_name: str, expected_sha256: str, size: int,
    pending_is_current: Callable[[], bool],
) -> UsbFilePublication:
    """Commit one stage already associated with a durable pending record.

    This internal filesystem step borrows both descriptors. The trusted Core
    caller must hold its per-reader lock, keep the descriptors alive and
    revalidate the pending record, exact scope, rights and consent via the
    callback. A published result proves the file only; the caller must still
    commit its receipt and observation. Any uncertain outcome after attempting
    a rename stays recoverable. This step never unlinks a stage or a target.
    """
    if (any(type(value) is not int or value < 0 for value in (directory_fd, staged_fd))
            or type(mount_id) is not int or mount_id <= 0
            or type(size) is not int or not 0 < size <= _MAX_FILE_BYTES
            or not _sha(expected_sha256)
            or type(staged_name) is not str or _STAGE_NAME.fullmatch(staged_name) is None
            or type(target_name) is not str
            or target_name not in {expected_sha256 + suffix for suffix in _SUFFIX.values()}
            or not callable(pending_is_current)):
        return UsbFilePublication("blocked", "usb_publish_request_invalid")
    if sys.platform != "linux" or not hasattr(os, "O_PATH") or not hasattr(os, "pread"):
        return UsbFilePublication("blocked", "usb_publish_unsupported")

    rename_attempted = False
    try:
        directory = os.fstat(directory_fd)
        staged = os.fstat(staged_fd)
        if (not stat.S_ISDIR(directory.st_mode) or staged.st_dev != directory.st_dev
                or linux_mount._fd_mount_id(directory_fd) != mount_id
                or linux_mount._fd_mount_id(staged_fd) != mount_id
                or not _same_file_at(directory_fd, staged_name, staged, mount_id)):
            return UsbFilePublication("blocked", "usb_publish_stage_unverified")
        verified = _verified_file(staged_fd, expected_sha256, size)
        if verified is None or _file_version(verified) != _file_version(staged):
            return UsbFilePublication("blocked", "usb_publish_stage_unverified")
        os.fsync(staged_fd)
        if pending_is_current() is not True:
            return UsbFilePublication("blocked", "usb_publish_pending_not_current")
        aliases = _target_aliases(directory_fd, target_name)
        if aliases is None:
            return UsbFilePublication("blocked", "usb_publish_directory_unbounded")
        if aliases:
            return UsbFilePublication("blocked", "usb_publish_target_occupied")
        if not _same_file_at(directory_fd, staged_name, verified, mount_id):
            return UsbFilePublication("blocked", "usb_publish_stage_changed")

        rename_attempted = True
        try:
            _rename_exclusive(directory_fd, staged_name, target_name)
        except OSError as error:
            if error.errno == errno.EEXIST:
                return UsbFilePublication("blocked", "usb_publish_target_occupied", error.errno)
            if error.errno in _UNSUPPORTED:
                return UsbFilePublication("blocked", "usb_publish_unsupported", error.errno)
            raise

        os.fsync(directory_fd)
        if (pending_is_current() is not True
                or _target_aliases(directory_fd, target_name) != (target_name,)):
            return UsbFilePublication("recovery_required", "usb_publish_postcheck_failed")
        published = _verified_file(staged_fd, expected_sha256, size)
        if published is None or not _same_file_at(directory_fd, target_name, published, mount_id):
            return UsbFilePublication("recovery_required", "usb_publish_postcheck_failed")
        return UsbFilePublication("published", "usb_publish_file_verified")
    except OSError as error:
        status = "recovery_required" if rename_attempted else "blocked"
        return UsbFilePublication(status, "usb_publish_io_failed", error.errno)
    except Exception as error:
        # A Core revalidation callback can fail after the namespace changed.
        # Keep its diagnostic and leave reconciliation to the pending record.
        status = "recovery_required" if rename_attempted else "blocked"
        return UsbFilePublication(
            status, "usb_publish_revalidation_failed", detail=str(error),
        )


__all__ = ["UsbFilePublication", "publish_usb_staged_file"]
