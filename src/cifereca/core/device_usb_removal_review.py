"""Read-only admission review for withdrawing a copy managed by Cifereca."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import time
from typing import Any, Callable, Literal

from .device_usb_copy_admission import UsbCopyConsent, UsbCopyRightsEvidence, _MAX_AGE
from .device_usb_copy_recovery import UsbCopyRecoveryPin, inspect_usb_copy_recovery
from .device_usb_copy_supervisor import UsbCopyJournal, UsbCopyJournalEntry, _coordinates
from .device_usb_mount import LinuxUsbMount, observe_linux_usb_mount
from .device_usb_preview import UsbManagedReceipt, _relative, _sha


_OPERATION_ID = re.compile(r"[0-9a-f]{32}", re.ASCII)


@dataclass(frozen=True, slots=True)
class UsbRemovalConsent:
    identity_id: str
    device_id: str
    usb_fingerprint: str
    artifact_id: str
    sha256: str
    target_relative_path: str
    copy_operation_id: str
    operation_id: str
    confirmed_at: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class UsbRemovalReview:
    status: Literal["review", "blocked", "absent"]
    reason: str
    executable: bool = False


def review_usb_removal(
    *, storage: Any, journal: UsbCopyJournal, mountpoint: Path,
    consent: UsbRemovalConsent, current_profile: Callable[[], tuple[str, int]],
) -> UsbRemovalReview:
    """Review a fresh withdrawal against an exact completed copy and its pin.

    This entry point never consumes a consent, creates a journal entry, or
    unlinks a file. Only a separate supervised executor can act after a new
    in-lock verification. A remount/reboot invalidates the current pin.
    """
    def blocked(reason: str) -> UsbRemovalReview:
        return UsbRemovalReview("blocked", reason)

    if (type(consent) is not UsbRemovalConsent or not isinstance(mountpoint, Path)
            or not mountpoint.is_absolute() or not callable(current_profile)
            or not _sha(consent.sha256) or consent.artifact_id != "sha256:" + consent.sha256
            or not _relative(consent.target_relative_path)
            or any(type(value) is not str or _OPERATION_ID.fullmatch(value) is None
                   for value in (consent.copy_operation_id, consent.operation_id))
            or consent.copy_operation_id == consent.operation_id):
        return blocked("usb_removal_confirmation_invalid")

    def fresh() -> bool:
        now = int(time.time())
        return (
            type(consent.confirmed_at) is int and type(consent.expires_at) is int
            and 0 <= now - consent.confirmed_at <= _MAX_AGE
            and now < consent.expires_at <= consent.confirmed_at + _MAX_AGE
        )

    def read_scope():
        binding = storage.device_binding_get(consent.device_id)
        intent = storage.device_placement_get(consent.device_id, consent.artifact_id)
        artifact = storage.artifact_get(consent.artifact_id)
        if not all(type(row) is dict for row in (binding, intent, artifact)):
            return None
        volume, owner = binding.get("metadata"), intent.get("metadata")
        managed = binding.get("target_directory")
        if (binding.get("identity_id") != consent.identity_id
                or binding.get("device_id") != consent.device_id or binding.get("enabled") is not True
                or type(volume) is not dict or volume.get("usb_fingerprint") != consent.usb_fingerprint
                or not _relative(managed)
                or PurePosixPath(consent.target_relative_path).parent != PurePosixPath(managed)
                or intent.get("device_id") != consent.device_id
                or intent.get("artifact_id") != consent.artifact_id
                or type(owner) is not dict or owner.get("intent_identity_id") != consent.identity_id
                or intent.get("desired_state") != "remove"
                or intent.get("target_relative_path") != consent.target_relative_path
                or artifact.get("artifact_id") != consent.artifact_id
                or artifact.get("sha256") != consent.sha256 or artifact.get("kind") != "file"):
            return None
        return binding, intent, artifact

    def database_revision() -> tuple[int, int]:
        with storage._lock, storage._connect() as db:
            return db.execute("PRAGMA data_version").fetchone()[0], db.total_changes

    try:
        profile = current_profile()
        if (type(profile) is not tuple or len(profile) != 2
                or profile[0] != consent.identity_id
                or type(profile[1]) is not int or profile[1] < 0 or not fresh()):
            return blocked("usb_removal_confirmation_invalid")
        revision = database_revision()
        initial = read_scope()
        if initial is None:
            return blocked("usb_removal_scope_unverified")
        binding, _, artifact = initial
        completed = journal.get(consent.copy_operation_id)
        final_version = getattr(completed, "final_version", None)
        if (type(completed) is not UsbCopyJournalEntry or completed.phase != "completed"
                or type(completed.consent) is not UsbCopyConsent
                or type(completed.rights) is not UsbCopyRightsEvidence
                or type(completed.pin) is not UsbCopyRecoveryPin
                or type(completed.receipt) is not UsbManagedReceipt
                or type(final_version) is not tuple or len(final_version) != 5
                or any(type(value) is not int for value in final_version)
                or journal.get(consent.operation_id) is not None):
            return blocked("usb_removal_receipt_unverified")

        identity, device, fingerprint = consent.identity_id, consent.device_id, consent.usb_fingerprint
        path, digest = consent.target_relative_path, consent.sha256
        copy, pin = completed.consent, completed.pin
        if (completed.receipt != UsbManagedReceipt(identity, device, fingerprint, path, digest)
                or (pin.identity_id, pin.device_id, pin.usb_fingerprint) != (identity, device, fingerprint)
                or (pin.target_relative_path, pin.sha256, pin.size) != (path, digest, completed.size)
                or type(pin.stage_version) is not tuple or len(pin.stage_version) != 5
                or final_version[:-1] != pin.stage_version[:-1]
                or pin.staged_name != ".cifereca-usb-" + consent.copy_operation_id + ".part"
                or copy.operation_id != consent.copy_operation_id or copy.operation != "upload"
                or copy.target_relative_path != path or copy.selected_format != artifact.get("media_format")
                or _coordinates(copy)[:5] != (identity, device, fingerprint, consent.artifact_id, digest)
                or completed.rights.personal_usb_copy_permitted is not True
                or _coordinates(completed.rights) != _coordinates(copy)
                or artifact.get("size") != completed.size):
            return blocked("usb_removal_receipt_unverified")

        mount = observe_linux_usb_mount(identity_id=identity, binding=binding, mountpoint=mountpoint)
        if (type(mount) is not LinuxUsbMount
                or (mount.identity_id, mount.device_id, mount.usb_fingerprint, mount.mountpoint)
                != (identity, device, fingerprint, mountpoint)):
            return blocked("usb_removal_mount_unverified")
        directory = mountpoint / PurePosixPath(path).parent
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            physical = inspect_usb_copy_recovery(
                directory_fd=fd, mount=mount, pin=pin, final_version=final_version,
            )
        finally:
            os.close(fd)

        final_mount = observe_linux_usb_mount(identity_id=identity, binding=binding, mountpoint=mountpoint)
        if (type(final_mount) is not LinuxUsbMount or final_mount.scope_key() != mount.scope_key()
                or current_profile() != profile or read_scope() != initial
                or journal.get(consent.copy_operation_id) != completed
                or journal.get(consent.operation_id) is not None
                or database_revision() != revision or not fresh()):
            return blocked("usb_removal_scope_changed")
        if physical.status == "missing":
            return UsbRemovalReview("absent", "usb_removal_exact_names_missing")
        if physical.status != "final_candidate":
            return blocked(physical.reason)
        return UsbRemovalReview("review", "usb_removal_receipt_and_copy_verified")
    except Exception:
        # Backend/journal callbacks can raise arbitrary ordinary exceptions.
        # A review must fail closed without propagating private paths, volume
        # identifiers or backend exception text to UI/Diagnostics. Never unlink.
        return blocked("usb_removal_review_unavailable")


__all__ = ["UsbRemovalConsent", "UsbRemovalReview", "review_usb_removal"]
