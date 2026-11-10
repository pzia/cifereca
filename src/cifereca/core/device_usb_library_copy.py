"""Core entry point from an approved library placement to a supervised USB copy."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import time
from typing import Any, Callable

from .device_usb_copy_admission import UsbCopyConsent, UsbCopyRightsEvidence, _MAX_AGE
from .device_usb_copy_supervisor import UsbCopyExecution, UsbCopyJournal, execute_usb_copy
from .device_usb_dry_run import inspect_usb_dry_run
from .device_usb_live_source import prepare_usb_live_source


def execute_usb_library_copy(
    *, storage: Any, journal: UsbCopyJournal | None = None, lock_root: Path, placement_id: str,
    mountpoint: Path, consent: UsbCopyConsent, rights: UsbCopyRightsEvidence,
    current_profile: Callable[[], tuple[str, Path, int]],
    policy_verifier: Callable[[UsbCopyRightsEvidence], bool] | None,
    plugin_manager: Any, plugin_id: str, registry_generation: int,
    check_cancelled: Callable[[], None] | None = None,
) -> UsbCopyExecution:
    """Compose Core's live source guard, fresh inventory and supervised write.

    Only a trusted Core caller provides the rights verifier and durable
    journal. The whole managed area is reviewed under the volume lock before
    consuming consent, so moved books are detected as well as name collisions.
    """
    if journal is None:
        from .device_usb_operation_journal import SqliteUsbOperationJournal
        journal = SqliteUsbOperationJournal(storage)
    source = prepare_usb_live_source(
        storage=storage, placement_id=placement_id, mountpoint=mountpoint,
        consent=consent, rights=rights, current_profile=current_profile,
        policy_verifier=policy_verifier,
    )
    if source is None:
        return UsbCopyExecution("blocked", "usb_copy_source_unverified")

    def review_target() -> bool:
        reviewed = inspect_usb_dry_run(
            storage=storage, identity_id=consent.identity_id, device_id=consent.device_id,
            artifact_id=consent.artifact_id, identity_root=source.identity_root,
            mountpoint=mountpoint,
        )
        if (reviewed.status != "scanned" or reviewed.scan_attested is not True
                or reviewed.executable is not False or len(reviewed.rows) != 1
                or type(reviewed.observed_at) is not int
                or not 0 <= int(time.time()) - reviewed.observed_at <= _MAX_AGE):
            return False
        row = reviewed.rows[0]
        return (
            row.artifact_id == consent.artifact_id and row.observed == "missing"
            and row.action == "upload_candidate" and row.executable is False
        )

    directory = mountpoint / PurePosixPath(consent.target_relative_path).parent
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except (OSError, AttributeError):
        # Do not expose a private volume path through an OSError string.
        return UsbCopyExecution("blocked", "usb_copy_directory_unavailable")
    try:
        return execute_usb_copy(
            journal=journal, lock_root=lock_root, mount=source.mount, directory_fd=fd,
            consent=consent, rights=rights, payload=source.payload,
            plugin_manager=plugin_manager, plugin_id=plugin_id, registry_generation=registry_generation,
            revalidate=source.revalidate, check_cancelled=check_cancelled, review_target=review_target,
        )
    finally:
        os.close(fd)


__all__ = ["execute_usb_library_copy"]
