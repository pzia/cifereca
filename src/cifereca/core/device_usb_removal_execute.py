"""Execute an explicitly confirmed withdrawal of a Core-owned USB copy.

Core owns the receipt, consent, mount, lock and operation journal. A target
plugin never receives a delete capability. Uncertain outcomes stay pending.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Callable, Literal, Protocol

from ..locking import InterProcessLock, LockBusyError
from .device_usb_copy_recovery import inspect_usb_copy_recovery
from .device_usb_file_commit import _file_version, _same_file_at
from .device_usb_mount import LinuxUsbMount, observe_linux_usb_mount
from .device_usb_preview import _sha
from .device_usb_removal_review import (
    UsbRemovalConsent, review_usb_removal,
)
from .device_usb_copy_supervisor import UsbCopyJournal, UsbCopyJournalEntry


@dataclass(frozen=True, slots=True)
class UsbRemovalJournalEntry:
    consent: UsbRemovalConsent
    phase: Literal["pending", "completed"] = "pending"


class UsbRemovalJournal(Protocol):
    def get(self, operation_id: str) -> UsbRemovalJournalEntry | None: ...
    def begin(self, entry: UsbRemovalJournalEntry) -> bool: ...
    def compare_and_set(self, before: UsbRemovalJournalEntry,
                        after: UsbRemovalJournalEntry) -> bool: ...


@dataclass(frozen=True, slots=True)
class UsbRemovalExecution:
    status: Literal["removed", "blocked", "recovery_required"]
    reason: str


def execute_usb_removal(
    *, storage: object, copy_journal: UsbCopyJournal | None = None,
    removal_journal: UsbRemovalJournal | None = None, lock_root: Path, mountpoint: Path,
    consent: UsbRemovalConsent,
    current_profile: Callable[[], tuple[str, int]],
) -> UsbRemovalExecution:
    """Withdraw only the exact copy inode whose receipt was committed by Core.

    The trusted caller provides a durable journal; persistence of begin/CAS
    is required. No automatic retry of a pending deletion can delete another
    file. A crash after unlink remains a recoverable uncertainty.
    """
    if (type(consent) is not UsbRemovalConsent or not isinstance(lock_root, Path)
            or not lock_root.is_absolute()
            or not isinstance(mountpoint, Path) or not mountpoint.is_absolute()
            or not callable(current_profile)
            or not _sha(consent.sha256)):
        return UsbRemovalExecution("blocked", "usb_removal_request_invalid")

    if copy_journal is None or removal_journal is None:
        from .device_usb_operation_journal import SqliteUsbOperationJournal
        durable = SqliteUsbOperationJournal(storage)
        copy_journal = copy_journal if copy_journal is not None else durable
        removal_journal = removal_journal if removal_journal is not None else durable

    lock_name = __import__("hashlib").sha256(
        consent.usb_fingerprint.encode("utf-8")
    ).hexdigest() + ".lock"
    reserved = False
    fd = None
    try:
        with InterProcessLock(lock_root / lock_name):
            if removal_journal.get(consent.operation_id) is not None:
                return UsbRemovalExecution("blocked", "usb_removal_operation_already_used")
            review = review_usb_removal(
                storage=storage, journal=copy_journal, mountpoint=mountpoint,
                consent=consent, current_profile=current_profile,
            )
            if review.status != "review":
                return UsbRemovalExecution("blocked", review.reason)

            binding = storage.device_binding_get(consent.device_id)
            if type(binding) is not dict:
                return UsbRemovalExecution("blocked", "usb_removal_binding_unverified")
            mount = observe_linux_usb_mount(
                identity_id=consent.identity_id, binding=binding,
                mountpoint=mountpoint,
            )
            if type(mount) is not LinuxUsbMount:
                return UsbRemovalExecution("blocked", "usb_removal_mount_unverified")
            copy = copy_journal.get(consent.copy_operation_id)
            if (type(copy) is not UsbCopyJournalEntry or copy.phase != "completed"
                    or copy.pin is None or copy.final_version is None):
                return UsbRemovalExecution("blocked", "usb_removal_receipt_unverified")
            target_name = PurePosixPath(consent.target_relative_path).name
            directory = mountpoint / PurePosixPath(consent.target_relative_path).parent
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY |
                         os.O_NOFOLLOW | os.O_CLOEXEC)
            reviewed = inspect_usb_copy_recovery(
                directory_fd=fd, mount=mount, pin=copy.pin,
                final_version=copy.final_version,
            )
            if reviewed.status != "final_candidate":
                return UsbRemovalExecution("blocked", reviewed.reason)
            before = os.stat(target_name, dir_fd=fd, follow_symlinks=False)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or _file_version(before) != copy.final_version
                    or not _same_file_at(fd, target_name, before, mount.mount_id)):
                return UsbRemovalExecution("blocked", "usb_removal_target_changed")

            # Re-review the current desired state under the reader lease.
            if review_usb_removal(
                storage=storage, journal=copy_journal, mountpoint=mountpoint,
                consent=consent, current_profile=current_profile,
            ).status != "review":
                return UsbRemovalExecution("blocked", "usb_removal_scope_changed")
            entry = UsbRemovalJournalEntry(consent)
            if removal_journal.begin(entry) is not True:
                return UsbRemovalExecution("blocked", "usb_removal_journal_conflict")
            reserved = True
            # Recheck the journal and inode immediately before unlink.
            if (removal_journal.get(consent.operation_id) != entry
                    or not _same_file_at(fd, target_name, before, mount.mount_id)):
                return UsbRemovalExecution("recovery_required", "usb_removal_pending_changed")
            os.unlink(target_name, dir_fd=fd)
            os.fsync(fd)
            completed = replace(entry, phase="completed")
            if removal_journal.compare_and_set(entry, completed) is not True:
                return UsbRemovalExecution("recovery_required", "usb_removal_commit_unverified")
            return UsbRemovalExecution("removed", "usb_removal_committed")
    except LockBusyError:
        return UsbRemovalExecution("blocked", "usb_removal_reader_busy")
    except (OSError, ValueError, TypeError, RuntimeError):
        return UsbRemovalExecution(
            "recovery_required" if reserved else "blocked",
            "usb_removal_operation_unverified",
        )
    finally:
        if fd is not None:
            os.close(fd)


__all__ = [
    "UsbRemovalExecution", "UsbRemovalJournalEntry", "UsbRemovalJournal",
    "execute_usb_removal",
]
