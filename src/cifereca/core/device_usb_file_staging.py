"""Core-owned staging file filled via an active storage.target service."""
from __future__ import annotations

from dataclasses import dataclass
import errno
from hashlib import sha256
import os
import stat
import sys
from threading import get_ident
from typing import Any, Callable, Literal

from ..plugin_api.usb_storage import USB_STAGE_CAPABILITY
from . import device_usb_mount as linux_mount
from .device_usb_content_snapshot import MAX_SNAPSHOT_BYTES
from .device_usb_file_commit import _STAGE_NAME, _same_file_at, _verified_file
from .device_usb_preview import _sha


@dataclass(frozen=True, slots=True)
class UsbFileStaging:
    status: Literal["staged", "blocked", "recovery_required"]
    reason: str
    staged_fd: int | None = None
    errno: int | None = None
    detail: str | None = None


def _stage_service(plugin_manager: Any, plugin_id: str, generation: int) -> object | None:
    if plugin_manager is None:
        return None
    matches = [
        row.service for row in plugin_manager.services("storage.target")
        if row.plugin_id == plugin_id and row.registry_generation == generation
        and USB_STAGE_CAPABILITY in row.capabilities
        and callable(getattr(row.service, "write_usb_stage", None))
    ]
    return matches[0] if len(matches) == 1 else None


def stage_usb_file(
    *, plugin_manager: Any, plugin_id: str, registry_generation: int,
    directory_fd: int, mount_id: int, staged_name: str,
    payload: bytes, expected_sha256: str, pending_is_current: Callable[[], bool],
    check_cancelled: Callable[[], None] | None = None,
) -> UsbFileStaging:
    """Write a new hidden stage after a trusted Core pending record is durable.

    The caller holds the reader lock and pinned directory lease, validates the
    exact pending operation and owns the returned descriptor on success.
    After creating a stage, failures leave it for supervised reconciliation.
    This operation does not publish a final name or update the receipt.
    """
    if (type(plugin_id) is not str or not plugin_id
            or type(registry_generation) is not int or registry_generation < 0
            or type(directory_fd) is not int or directory_fd < 0
            or type(mount_id) is not int or mount_id <= 0
            or type(staged_name) is not str or _STAGE_NAME.fullmatch(staged_name) is None
            or type(payload) is not bytes or not 0 < len(payload) <= MAX_SNAPSHOT_BYTES
            or not _sha(expected_sha256)
            or not callable(pending_is_current)
            or (check_cancelled is not None and not callable(check_cancelled))):
        return UsbFileStaging("blocked", "usb_stage_request_invalid")
    if sys.platform != "linux" or not hasattr(os, "O_PATH") or not hasattr(os, "pread"):
        return UsbFileStaging("blocked", "usb_stage_unsupported")
    if sha256(payload).hexdigest() != expected_sha256:
        return UsbFileStaging("blocked", "usb_stage_source_mismatch")

    fd = None
    creation_attempted = False
    try:
        service = _stage_service(plugin_manager, plugin_id, registry_generation)
        if service is None:
            return UsbFileStaging("blocked", "usb_stage_transport_unavailable")
        if (not stat.S_ISDIR(os.fstat(directory_fd).st_mode)
                or linux_mount._fd_mount_id(directory_fd) != mount_id):
            return UsbFileStaging("blocked", "usb_stage_directory_unverified")
        if check_cancelled is not None:
            check_cancelled()
        if pending_is_current() is not True:
            return UsbFileStaging("blocked", "usb_stage_pending_not_current")
        creation_attempted = True
        try:
            fd = os.open(
                staged_name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=directory_fd,
            )
        except FileExistsError:
            return UsbFileStaging("recovery_required", "usb_stage_already_exists", errno=errno.EEXIST)

        offset = 0
        owner_thread = get_ident()
        accepting = True

        def append(chunk: bytes) -> None:
            nonlocal offset
            if not accepting or get_ident() != owner_thread:
                raise ValueError("USB staging sink is outside its synchronous call")
            if check_cancelled is not None:
                check_cancelled()
            if (type(chunk) is not bytes or not chunk
                    or len(chunk) > len(payload) - offset
                    or chunk != payload[offset:offset + len(chunk)]):
                raise ValueError("USB staging chunk does not match the verified source")
            view = memoryview(chunk)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError(errno.EIO, "USB staging write made no progress")
                view = view[written:]
            offset += len(chunk)

        try:
            outcome = service.write_usb_stage(payload, append)
        finally:
            accepting = False
        if outcome is not None or offset != len(payload):
            return UsbFileStaging("recovery_required", "usb_stage_incomplete")
        if check_cancelled is not None:
            check_cancelled()
        os.fsync(fd)
        os.fsync(directory_fd)
        if (_stage_service(plugin_manager, plugin_id, registry_generation) is not service
                or pending_is_current() is not True):
            return UsbFileStaging("recovery_required", "usb_stage_scope_changed")
        verified = _verified_file(fd, expected_sha256, len(payload))
        if verified is None or not _same_file_at(directory_fd, staged_name, verified, mount_id):
            return UsbFileStaging("recovery_required", "usb_stage_file_unverified")
        result = UsbFileStaging("staged", "usb_stage_file_verified", staged_fd=fd)
        fd = None
        return result
    except OSError as error:
        status = "recovery_required" if creation_attempted else "blocked"
        return UsbFileStaging(status, "usb_stage_io_failed", errno=error.errno)
    except Exception:
        # A storage plugin may put private paths or credentials in exceptions.
        status = "recovery_required" if creation_attempted else "blocked"
        return UsbFileStaging(status, "usb_stage_transport_failed")
    finally:
        if fd is not None:
            os.close(fd)


__all__ = ["UsbFileStaging", "stage_usb_file"]
