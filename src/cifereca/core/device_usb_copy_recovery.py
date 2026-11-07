"""Read-only reconciliation of a journaled USB stage or its renamed file."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Callable, Literal
from uuid import UUID

from . import device_usb_mount as linux_mount
from .device_usb_file_commit import (
    UsbFilePublication, _STAGE_NAME, _file_version, _same_file_at, _verified_file,
    publish_usb_staged_file,
)
from .device_usb_mount import LinuxUsbMount
from .device_usb_preview import _relative, _sha
from .device_usb_target_preflight import _MAX_FILE_BYTES, _target_aliases
from .device_usb_variant_review import _SUFFIX


@dataclass(frozen=True, slots=True)
class UsbCopyRecoveryPin:
    identity_id: str
    device_id: str
    usb_fingerprint: str
    staged_name: str
    target_relative_path: str
    sha256: str
    size: int
    boot_id: str
    mount_id: int
    directory_inode: tuple[int, int]
    stage_version: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class UsbCopyRecoveryReview:
    status: Literal["unverified", "missing", "conflict", "stage_candidate", "final_candidate"]
    reason: str


def _boot_id() -> str:
    with Path("/proc/sys/kernel/random/boot_id").open(encoding="ascii") as handle:
        return str(UUID(handle.read(64).strip()))


def _valid_names(staged_name: str, target: str, digest: str, size: int) -> bool:
    return (
        type(staged_name) is str and _STAGE_NAME.fullmatch(staged_name) is not None
        and _relative(target) and "/" in target and _sha(digest)
        and PurePosixPath(target).name in {digest + suffix for suffix in _SUFFIX.values()}
        and type(size) is int and 0 < size <= _MAX_FILE_BYTES
    )


def _directory_stat(
    fd: int, mount: LinuxUsbMount, target: str,
) -> os.stat_result | None:
    current = os.fstat(fd)
    expected = mount.mountpoint / PurePosixPath(target).parent
    if (not stat.S_ISDIR(current.st_mode)
            or current.st_dev != os.makedev(mount.major, mount.minor)
            or linux_mount._fd_mount_id(fd) != mount.mount_id
            or expected.resolve(strict=True) != expected):
        return None
    live_fd = os.open(expected, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        live = os.fstat(live_fd)
        if (not stat.S_ISDIR(live.st_mode) or _file_version(live) != _file_version(current)
                or linux_mount._fd_mount_id(live_fd) != mount.mount_id):
            return None
        return current
    finally:
        os.close(live_fd)


def capture_usb_copy_recovery_pin(
    *, directory_fd: int, staged_fd: int, mount: LinuxUsbMount,
    staged_name: str, target_relative_path: str, sha256: str, size: int,
) -> UsbCopyRecoveryPin | None:
    """Capture the exact stage after fsync and before publishing its name.

    The caller supplies the current Core-attested mount and persists the
    result under the existing pending operation before attempting rename.
    """
    if (not hasattr(os, "O_PATH") or type(mount) is not LinuxUsbMount
            or not _valid_names(staged_name, target_relative_path, sha256, size)):
        return None
    try:
        directory = _directory_stat(directory_fd, mount, target_relative_path)
        if directory is None:
            return None
        checked = _verified_file(staged_fd, sha256, size)
        if (checked is None or linux_mount._fd_mount_id(staged_fd) != mount.mount_id
                or not _same_file_at(directory_fd, staged_name, checked, mount.mount_id)):
            return None
        current = _directory_stat(directory_fd, mount, target_relative_path)
        if current is None or _file_version(current) != _file_version(directory):
            return None
        return UsbCopyRecoveryPin(
            mount.identity_id, mount.device_id, mount.usb_fingerprint,
            staged_name, target_relative_path, sha256, size, _boot_id(), mount.mount_id,
            (current.st_dev, current.st_ino), _file_version(checked),
        )
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def publish_usb_pending_copy(
    *, directory_fd: int, staged_fd: int, mount: LinuxUsbMount,
    staged_name: str, target_relative_path: str, sha256: str, size: int,
    checkpoint_pending: Callable[[UsbCopyRecoveryPin], bool],
    pending_is_current: Callable[[UsbCopyRecoveryPin], bool],
) -> UsbFilePublication:
    """Persist the verified stage pin on an existing pending copy, then publish.

    The trusted Core journal attaches the pin to the exact existing operation
    under its reader lock and returns True only after durable commit. The
    second callback rereads that operation and its authorization. A filesystem
    success still requires a distinct managed-copy receipt.
    """
    pin = capture_usb_copy_recovery_pin(
        directory_fd=directory_fd, staged_fd=staged_fd, mount=mount,
        staged_name=staged_name, target_relative_path=target_relative_path,
        sha256=sha256, size=size,
    )
    if pin is None:
        return UsbFilePublication("blocked", "usb_recovery_pin_unverified")
    try:
        if checkpoint_pending(pin) is not True:
            return UsbFilePublication("blocked", "usb_recovery_checkpoint_refused")
    except Exception as error:
        return UsbFilePublication(
            "blocked", "usb_recovery_checkpoint_failed", detail=str(error),
        )

    def current() -> bool:
        if pending_is_current(pin) is not True or pin.boot_id != _boot_id():
            return False
        directory = _directory_stat(directory_fd, mount, pin.target_relative_path)
        if directory is None or (directory.st_dev, directory.st_ino) != pin.directory_inode:
            return False
        version = _file_version(os.fstat(staged_fd))
        try:
            os.stat(pin.staged_name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            # After the rename, ctime may differ. The publisher independently
            # verifies the final name before returning its physical result.
            return version[:-1] == pin.stage_version[:-1]
        return version == pin.stage_version

    return publish_usb_staged_file(
        directory_fd=directory_fd, staged_fd=staged_fd, mount_id=pin.mount_id,
        staged_name=pin.staged_name, target_name=PurePosixPath(pin.target_relative_path).name,
        expected_sha256=pin.sha256, size=pin.size, pending_is_current=current,
    )


def inspect_usb_copy_recovery(
    *, directory_fd: int, mount: LinuxUsbMount, pin: UsbCopyRecoveryPin,
) -> UsbCopyRecoveryReview:
    """Review the same mount's names without adopting, renaming or unlinking.

    A final match describes a physical candidate for supervised recovery.
    It never grants a managed-copy receipt or the right to remove the file.
    A reboot/remount requires a new inspection outside this narrow proof.
    """
    unknown = UsbCopyRecoveryReview("unverified", "usb_recovery_scope_unverified")
    if (not hasattr(os, "O_PATH") or type(mount) is not LinuxUsbMount or type(pin) is not UsbCopyRecoveryPin
            or (mount.identity_id, mount.device_id, mount.usb_fingerprint, mount.mount_id)
            != (pin.identity_id, pin.device_id, pin.usb_fingerprint, pin.mount_id)
            or not _valid_names(pin.staged_name, pin.target_relative_path, pin.sha256, pin.size)):
        return unknown
    try:
        if pin.boot_id != _boot_id():
            return unknown
        start = _directory_stat(directory_fd, mount, pin.target_relative_path)
        if start is None or (start.st_dev, start.st_ino) != pin.directory_inode:
            return unknown
        target_name = PurePosixPath(pin.target_relative_path).name
        stage_entries = _target_aliases(directory_fd, pin.staged_name)
        final_entries = _target_aliases(directory_fd, target_name)
        if stage_entries is None or final_entries is None:
            return UsbCopyRecoveryReview("unverified", "usb_recovery_directory_unbounded")
        if (stage_entries not in ((), (pin.staged_name,))
                or final_entries not in ((), (target_name,))
                or (stage_entries and final_entries)):
            return UsbCopyRecoveryReview("conflict", "usb_recovery_names_conflict")
        if not stage_entries and not final_entries:
            result = UsbCopyRecoveryReview("missing", "usb_recovery_names_absent")
        else:
            name = pin.staged_name if stage_entries else target_name
            marker_fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
            try:
                marker = os.fstat(marker_fd)
                version = _file_version(marker)
                # Rename may advance ctime. An unchanged stage must match it
                # exactly, and a final name must still match the inode + mtime.
                matching = (
                    version == pin.stage_version if stage_entries
                    else version[:-1] == pin.stage_version[:-1]
                )
                if (not matching or not stat.S_ISREG(marker.st_mode)
                        or linux_mount._fd_mount_id(marker_fd) != pin.mount_id):
                    return UsbCopyRecoveryReview("conflict", "usb_recovery_file_unattributed")
                # Open the already pinned inode, never an independently
                # resolved path that could now name a different file.
                fd = os.open(f"/proc/self/fd/{marker_fd}", os.O_RDONLY | os.O_CLOEXEC)
                try:
                    checked = _verified_file(fd, pin.sha256, pin.size)
                    if (checked is None or _file_version(checked) != version
                            or not _same_file_at(directory_fd, name, checked, pin.mount_id)):
                        return UsbCopyRecoveryReview("conflict", "usb_recovery_file_unattributed")
                finally:
                    os.close(fd)
            finally:
                os.close(marker_fd)
            result = (
                UsbCopyRecoveryReview("stage_candidate", "usb_recovery_stage_matches")
                if stage_entries else UsbCopyRecoveryReview("final_candidate", "usb_recovery_final_matches")
            )
        end = _directory_stat(directory_fd, mount, pin.target_relative_path)
        return result if end is not None and _file_version(end) == _file_version(start) else unknown
    except (OSError, ValueError, TypeError, OverflowError):
        return unknown


__all__ = [
    "UsbCopyRecoveryPin", "UsbCopyRecoveryReview",
    "capture_usb_copy_recovery_pin", "inspect_usb_copy_recovery", "publish_usb_pending_copy",
]
