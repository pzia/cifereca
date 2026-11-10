"""Supervise a single USB copy through a durable Core operation journal."""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import time
from typing import Any, Callable, Literal, Protocol

from ..locking import InterProcessLock, LockBusyError
from .device_usb_content_snapshot import MAX_SNAPSHOT_BYTES
from .device_usb_copy_admission import UsbCopyConsent, UsbCopyRightsEvidence, _MAX_AGE, _text
from .device_usb_copy_recovery import (
    UsbCopyRecoveryPin, _directory_stat, _valid_names, publish_usb_pending_copy,
)
from .device_usb_file_commit import _file_version, _same_file_at, _verified_file
from .device_usb_file_staging import _stage_service, stage_usb_file
from .device_usb_mount import LinuxUsbMount
from .device_usb_preview import UsbManagedReceipt
from .device_usb_target_preflight import _target_aliases
from .device_usb_variant_review import _SUFFIX


@dataclass(frozen=True, slots=True)
class UsbCopyJournalEntry:
    consent: UsbCopyConsent
    rights: UsbCopyRightsEvidence
    size: int
    phase: Literal["pending", "checkpointed", "completed"] = "pending"
    pin: UsbCopyRecoveryPin | None = None
    receipt: UsbManagedReceipt | None = None
    final_version: tuple[int, ...] | None = None


class UsbCopyJournal(Protocol):
    """Durable backend owned by Core, never provided by a transport plugin."""

    def begin(self, entry: UsbCopyJournalEntry) -> bool:
        """Insert once by operation id and reserve the volume/target while pending."""
        ...

    def get(self, operation_id: str) -> UsbCopyJournalEntry | None: ...

    def compare_and_set(self, before: UsbCopyJournalEntry, after: UsbCopyJournalEntry) -> bool:
        """Commit the exact transition. Completion includes its receipt/observation."""
        ...


@dataclass(frozen=True, slots=True)
class UsbCopyExecution:
    status: Literal["completed", "blocked", "recovery_required"]
    reason: str
    receipt: UsbManagedReceipt | None = None
    detail: str | None = None


def _coordinates(value: UsbCopyConsent | UsbCopyRightsEvidence) -> tuple[str, ...]:
    return (
        value.identity_id, value.device_id, value.usb_fingerprint,
        value.artifact_id, value.sha256, value.selected_format,
    )


def _authorization_fresh(entry: UsbCopyJournalEntry) -> bool:
    consent, rights = entry.consent, entry.rights
    now = int(time.time())
    return (
        type(consent.confirmed_at) is int and type(consent.expires_at) is int
        and 0 <= now - consent.confirmed_at <= _MAX_AGE
        and now < consent.expires_at <= consent.confirmed_at + _MAX_AGE
        and type(rights.observed_at) is int and type(rights.valid_until) is int
        and 0 <= now - rights.observed_at <= _MAX_AGE
        and now < rights.valid_until <= now + _MAX_AGE
        and (rights.loan_expires_at is not None or rights.source_kind == "purchase")
        and (rights.loan_expires_at is None
             or (type(rights.loan_expires_at) is int and now < rights.loan_expires_at))
    )


def execute_usb_copy(
    *, journal: UsbCopyJournal, lock_root: Path, mount: LinuxUsbMount,
    directory_fd: int, consent: UsbCopyConsent, rights: UsbCopyRightsEvidence,
    payload: bytes, plugin_manager: Any, plugin_id: str, registry_generation: int,
    revalidate: Callable[[UsbCopyConsent, UsbCopyRightsEvidence], bool],
    review_target: Callable[[], bool],
    check_cancelled: Callable[[], None] | None = None,
) -> UsbCopyExecution:
    """Copy verified bytes under a reader lock and an insert-only consent.

    The trusted Core caller supplies its private lock directory, current mount,
    authorized source bytes and a live revalidation callback for identity,
    binding, intention, source and independent copy policy. The target review
    runs under the volume lock before the journal reserves the copy. Each
    journal transition must be durable before
    returning True. Uncertain or failed writes are left for supervised review.
    """
    if (type(consent) is not UsbCopyConsent or type(rights) is not UsbCopyRightsEvidence
            or type(mount) is not LinuxUsbMount or not isinstance(lock_root, Path)
            or not lock_root.is_absolute() or not callable(revalidate)
            or type(directory_fd) is not int or directory_fd < 0
            or type(plugin_id) is not str or not plugin_id
            or type(registry_generation) is not int or registry_generation < 0
            or (check_cancelled is not None and not callable(check_cancelled))
            or not callable(review_target)
            or type(consent.operation_id) is not str or type(payload) is not bytes
            or not 0 < len(payload) <= MAX_SNAPSHOT_BYTES):
        return UsbCopyExecution("blocked", "usb_copy_request_invalid")
    stage_name = ".cifereca-usb-" + consent.operation_id + ".part"
    target = consent.target_relative_path
    if (not _valid_names(stage_name, target, consent.sha256, len(payload))
            or type(consent.selected_format) is not str or consent.selected_format not in _SUFFIX
            or PurePosixPath(target).name != consent.sha256 + _SUFFIX[consent.selected_format]
            or consent.operation != "upload" or consent.artifact_id != "sha256:" + consent.sha256
            or _coordinates(consent) != _coordinates(rights)
            or _coordinates(consent)[:3] != (mount.identity_id, mount.device_id, mount.usb_fingerprint)
            or rights.personal_usb_copy_permitted is not True
            or rights.source_kind not in ("loan", "purchase")
            or not _text(rights.source_reference_id) or not _text(rights.policy_ref)
            or sha256(payload).hexdigest() != consent.sha256):
        return UsbCopyExecution("blocked", "usb_copy_request_invalid")

    entry = UsbCopyJournalEntry(consent, rights, len(payload))
    lock_name = sha256(consent.usb_fingerprint.encode("utf-8")).hexdigest() + ".lock"
    journal_touched = False
    stage_fd = None
    try:
        with InterProcessLock(lock_root / lock_name):
            def authorized() -> bool:
                if check_cancelled is not None:
                    check_cancelled()
                return (
                    _authorization_fresh(entry)
                    and revalidate(consent, rights) is True
                    and _authorization_fresh(entry)
                    and _directory_stat(directory_fd, mount, target) is not None
                )

            if not authorized():
                return UsbCopyExecution("blocked", "usb_copy_authorization_unverified")
            service = _stage_service(plugin_manager, plugin_id, registry_generation)
            if service is None:
                return UsbCopyExecution("blocked", "usb_stage_transport_unavailable")
            if journal.get(consent.operation_id) is not None:
                return UsbCopyExecution("blocked", "usb_copy_consent_already_used")
            if review_target() is not True or not authorized():
                return UsbCopyExecution("blocked", "usb_copy_target_review_refused")
            if _target_aliases(directory_fd, PurePosixPath(target).name) != ():
                return UsbCopyExecution("blocked", "usb_copy_target_not_clear")

            journal_touched = True
            if journal.begin(entry) is not True:
                return UsbCopyExecution("blocked", "usb_copy_journal_conflict")

            def current() -> bool:
                return (authorized()
                        and _stage_service(plugin_manager, plugin_id, registry_generation) is service
                        and journal.get(consent.operation_id) == entry)

            staged = stage_usb_file(
                plugin_manager=plugin_manager, plugin_id=plugin_id,
                registry_generation=registry_generation, directory_fd=directory_fd,
                mount_id=mount.mount_id, staged_name=stage_name,
                payload=payload, expected_sha256=consent.sha256,
                pending_is_current=current, check_cancelled=check_cancelled,
            )
            stage_fd = staged.staged_fd
            if staged.status != "staged" or stage_fd is None:
                return UsbCopyExecution("recovery_required", staged.reason, detail=staged.detail)

            def checkpoint(pin: UsbCopyRecoveryPin) -> bool:
                nonlocal entry
                after = replace(entry, phase="checkpointed", pin=pin)
                if not current() or journal.compare_and_set(entry, after) is not True:
                    return False
                entry = after
                return True

            physical = publish_usb_pending_copy(
                directory_fd=directory_fd, staged_fd=stage_fd, mount=mount,
                staged_name=stage_name, target_relative_path=target,
                sha256=consent.sha256, size=len(payload), checkpoint_pending=checkpoint,
                pending_is_current=lambda pin: entry.pin == pin and current(),
            )
            if physical.status != "published":
                return UsbCopyExecution("recovery_required", physical.reason, detail=physical.detail)

            receipt = UsbManagedReceipt(
                consent.identity_id, consent.device_id, consent.usb_fingerprint,
                target, consent.sha256,
            )
            if not current() or entry.pin is None:
                return UsbCopyExecution("recovery_required", "usb_copy_receipt_uncommitted")
            # Revalidation can outlive the publisher's check. Require the
            # same physical bytes/inode immediately before committing B.
            final_name = PurePosixPath(target).name
            verified = _verified_file(stage_fd, consent.sha256, len(payload))
            if (verified is None or _file_version(verified)[:-1] != entry.pin.stage_version[:-1]
                    or _target_aliases(directory_fd, final_name) != (final_name,)
                    or _directory_stat(directory_fd, mount, target) is None
                    or not _same_file_at(directory_fd, final_name, verified, mount.mount_id)):
                return UsbCopyExecution("recovery_required", "usb_copy_final_changed")
            completed = replace(
                entry, phase="completed", receipt=receipt, final_version=_file_version(verified),
            )
            if (not _authorization_fresh(entry)
                    or _stage_service(plugin_manager, plugin_id, registry_generation) is not service
                    or journal.compare_and_set(entry, completed) is not True
                    or journal.get(consent.operation_id) != completed):
                return UsbCopyExecution("recovery_required", "usb_copy_receipt_uncommitted")
            return UsbCopyExecution("completed", "usb_copy_receipt_committed", receipt=receipt)
    except LockBusyError:
        return UsbCopyExecution("blocked", "usb_copy_reader_busy")
    except Exception:
        # Journal/policy callbacks are untrusted as public diagnostic text.
        status = "recovery_required" if journal_touched else "blocked"
        return UsbCopyExecution(status, "usb_copy_operation_failed")
    finally:
        if stage_fd is not None:
            os.close(stage_fd)


__all__ = ["UsbCopyExecution", "UsbCopyJournal", "UsbCopyJournalEntry", "execute_usb_copy"]
