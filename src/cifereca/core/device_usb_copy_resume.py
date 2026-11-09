"""Resume a Core-journaled USB stage or post-rename copy (#4984).

Only an existing fsynced checkpoint can be completed. No new permission,
target replacement or receipt inferred from bytes is allowed.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from ..locking import InterProcessLock, LockBusyError
from .device_usb_copy_recovery import _directory_stat, inspect_usb_copy_recovery
from .device_usb_copy_supervisor import (
    UsbCopyExecution, UsbCopyJournal, UsbCopyJournalEntry,
    _authorization_fresh, _coordinates,
)
from .device_usb_file_commit import (
    _file_version, _same_file_at, _verified_file, publish_usb_staged_file,
)
from .device_usb_file_staging import _stage_service
from .device_usb_mount import LinuxUsbMount
from .device_usb_preview import UsbManagedReceipt


def reconcile_usb_pending_copy(
    *, journal: UsbCopyJournal, lock_root: Path, mount: LinuxUsbMount,
    directory_fd: int, operation_id: str, plugin_manager: Any,
    plugin_id: str, registry_generation: int,
    revalidate: Callable[[Any, Any], bool],
    check_cancelled: Callable[[], None] | None = None,
) -> UsbCopyExecution:
    """Finalize a checkpoint on the same mount after live Core revalidation."""
    if (type(mount) is not LinuxUsbMount
            or not isinstance(lock_root, Path) or not lock_root.is_absolute()
            or type(directory_fd) is not int or directory_fd < 0
            or type(operation_id) is not str or not operation_id
            or type(plugin_id) is not str or not plugin_id
            or type(registry_generation) is not int or registry_generation < 0
            or not callable(revalidate)
            or (check_cancelled is not None and not callable(check_cancelled))):
        return UsbCopyExecution("blocked", "usb_copy_recovery_request_invalid")
    lock_name = sha256(mount.usb_fingerprint.encode()).hexdigest() + ".lock"
    fd = None
    try:
        with InterProcessLock(lock_root / lock_name):
            entry = journal.get(operation_id)
            if (type(entry) is not UsbCopyJournalEntry
                    or entry.phase != "checkpointed" or entry.pin is None
                    or entry.consent.operation_id != operation_id
                    or _coordinates(entry.consent) != _coordinates(entry.rights)
                    or _coordinates(entry.consent)[:3] != (
                        mount.identity_id, mount.device_id, mount.usb_fingerprint,
                    )):
                return UsbCopyExecution("blocked", "usb_copy_recovery_not_checkpointed")
            pin = entry.pin
            if ((pin.identity_id, pin.device_id, pin.usb_fingerprint, pin.mount_id)
                    != (mount.identity_id, mount.device_id,
                        mount.usb_fingerprint, mount.mount_id)):
                return UsbCopyExecution("blocked", "usb_copy_recovery_mount_changed")

            service = _stage_service(plugin_manager, plugin_id, registry_generation)
            if service is None:
                return UsbCopyExecution("blocked", "usb_copy_recovery_transport_unavailable")

            def current() -> bool:
                if check_cancelled is not None:
                    check_cancelled()
                return (
                    _authorization_fresh(entry)
                    and revalidate(entry.consent, entry.rights) is True
                    and _stage_service(plugin_manager, plugin_id, registry_generation) is service
                    and journal.get(operation_id) == entry
                    and _directory_stat(directory_fd, mount, pin.target_relative_path)
                    is not None
                )

            if not current():
                return UsbCopyExecution("blocked", "usb_copy_recovery_authorization_changed")
            state = inspect_usb_copy_recovery(
                directory_fd=directory_fd, mount=mount, pin=pin,
            )
            final_name = PurePosixPath(pin.target_relative_path).name
            if state.status == "stage_candidate":
                fd = os.open(pin.staged_name, os.O_RDWR | os.O_NOFOLLOW |
                             os.O_CLOEXEC, dir_fd=directory_fd)
                stage = _verified_file(fd, pin.sha256, pin.size)
                if (stage is None or _file_version(stage) != pin.stage_version
                        or not _same_file_at(directory_fd, pin.staged_name,
                                             stage, pin.mount_id)
                        or not current()):
                    return UsbCopyExecution("recovery_required", "usb_copy_recovery_stage_changed")
                published = publish_usb_staged_file(
                    directory_fd=directory_fd, staged_fd=fd,
                    mount_id=pin.mount_id, staged_name=pin.staged_name,
                    target_name=final_name, expected_sha256=pin.sha256,
                    size=pin.size, pending_is_current=current,
                )
                if published.status != "published":
                    return UsbCopyExecution("recovery_required", published.reason)
                state = inspect_usb_copy_recovery(
                    directory_fd=directory_fd, mount=mount, pin=pin,
                )
            if state.status != "final_candidate":
                return UsbCopyExecution("recovery_required", state.reason)
            if fd is not None:
                os.close(fd)
                fd = None
            fd = os.open(final_name, os.O_RDONLY | os.O_NOFOLLOW |
                         os.O_CLOEXEC, dir_fd=directory_fd)
            final = _verified_file(fd, pin.sha256, pin.size)
            if (final is None
                    or _file_version(final)[:-1] != pin.stage_version[:-1]
                    or not _same_file_at(directory_fd, final_name, final, pin.mount_id)
                    or not current()):
                return UsbCopyExecution("recovery_required", "usb_copy_recovery_final_changed")
            receipt = UsbManagedReceipt(
                entry.consent.identity_id, entry.consent.device_id,
                entry.consent.usb_fingerprint,
                entry.consent.target_relative_path, entry.consent.sha256,
            )
            after = replace(
                entry, phase="completed", receipt=receipt,
                final_version=_file_version(final),
            )
            if (journal.compare_and_set(entry, after) is not True
                    or journal.get(operation_id) != after):
                return UsbCopyExecution("recovery_required", "usb_copy_recovery_receipt_uncommitted")
            return UsbCopyExecution("completed", "usb_copy_recovery_committed", receipt=receipt)
    except LockBusyError:
        return UsbCopyExecution("blocked", "usb_copy_reader_busy")
    except (OSError, ValueError, TypeError):
        return UsbCopyExecution("recovery_required", "usb_copy_recovery_io_unverified")
    finally:
        if fd is not None:
            os.close(fd)


__all__ = ["reconcile_usb_pending_copy"]
