"""P1 #4984: converge one USB reader through the existing Core primitives.

This is orchestration, not a second source of wishes, observed state, rights,
transport, or receipts. Each action is revalidated by its own Core executor.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .device_usb_copy_admission import UsbCopyConsent, UsbCopyRightsEvidence
from .device_usb_dry_run import inspect_usb_dry_run
from .device_usb_library_copy import execute_usb_library_copy
from .device_usb_observation_record import record_verified_usb_observation
from .device_usb_removal_execute import execute_usb_removal
from .device_usb_removal_review import UsbRemovalConsent
from .device_usb_operation_journal import SqliteUsbOperationJournal


@dataclass(frozen=True, slots=True)
class UsbSyncResult:
    status: str
    copied: int = 0
    removed: int = 0
    pending: int = 0
    failed: int = 0
    observed: bool = False


def synchronize_usb_reader(
    *, storage: Any, identity_id: str, device_id: str,
    identity_root: Path, mountpoint: Path, lock_root: Path,
    current_profile: Callable[[], tuple[str, Path, int]],
    plugin_manager: Any, plugin_id: str, registry_generation: int,
    confirmations: Mapping[str, UsbCopyConsent | UsbRemovalConsent],
    rights: Mapping[str, UsbCopyRightsEvidence],
    policy_verifier: Callable[[UsbCopyRightsEvidence], bool] | None,
    check_cancelled: Callable[[], None] | None = None,
) -> UsbSyncResult:
    """Apply only exact per-book confirmations from a fresh advisory delta.

    No confirmation means pending, not an implicit authorization. A scan
    failure never becomes an empty delta or a successfully synced reader.
    """
    if (not isinstance(identity_root, Path) or not identity_root.is_absolute()
            or not isinstance(mountpoint, Path) or not mountpoint.is_absolute()
            or not isinstance(lock_root, Path) or not lock_root.is_absolute()
            or type(identity_id) is not str or not identity_id
            or type(device_id) is not str or not device_id
            or not callable(current_profile)
            or not isinstance(confirmations, Mapping)
            or not isinstance(rights, Mapping)):
        return UsbSyncResult("blocked")

    preview = inspect_usb_dry_run(
        storage=storage, identity_id=identity_id, device_id=device_id,
        identity_root=identity_root, mountpoint=mountpoint,
    )
    if (preview.status != "scanned" or preview.scan_attested is not True
            or preview.executable is not False):
        return UsbSyncResult("unavailable")

    journal = SqliteUsbOperationJournal(storage)
    placements = storage.device_placements(device_id=device_id)
    placement_ids = {
        row["artifact_id"]: row.get("placement_id")
        for row in placements
        if type(row) is dict and type(row.get("artifact_id")) is str
    }
    copied = removed = pending = failed = 0
    for row in preview.rows:
        if check_cancelled is not None:
            check_cancelled()
        if row.action not in ("upload_candidate", "remove_candidate"):
            if row.action not in ("none", "up_to_date", "noop"):
                pending += 1
            continue
        consent = confirmations.get(row.artifact_id)
        if row.action == "upload_candidate":
            authorization = rights.get(row.artifact_id)
            placement_id = placement_ids.get(row.artifact_id)
            if (type(consent) is not UsbCopyConsent
                    or type(authorization) is not UsbCopyRightsEvidence
                    or type(placement_id) is not str or not placement_id):
                pending += 1
                continue
            result = execute_usb_library_copy(
                storage=storage, journal=journal, lock_root=lock_root,
                placement_id=placement_id, mountpoint=mountpoint,
                consent=consent, rights=authorization,
                current_profile=current_profile,
                policy_verifier=policy_verifier,
                plugin_manager=plugin_manager, plugin_id=plugin_id,
                registry_generation=registry_generation,
                check_cancelled=check_cancelled,
            )
            if result.status == "completed":
                copied += 1
            else:
                failed += 1
        else:
            if type(consent) is not UsbRemovalConsent:
                pending += 1
                continue
            result = execute_usb_removal(
                storage=storage, copy_journal=journal, removal_journal=journal,
                lock_root=lock_root, mountpoint=mountpoint, consent=consent,
                current_profile=lambda: (
                    current_profile()[0], current_profile()[2],
                ),
            )
            if result.status == "removed":
                removed += 1
            else:
                failed += 1

    observed = False
    if copied or removed:
        verification = record_verified_usb_observation(
            storage=storage, identity_id=identity_id, device_id=device_id,
            mountpoint=mountpoint,
        )
        observed = verification.status == "recorded"
        if not observed:
            failed += 1  # Never equate a file operation with observed presence.
    return UsbSyncResult(
        "partial" if failed else "pending" if pending else "completed",
        copied=copied, removed=removed, pending=pending,
        failed=failed, observed=observed,
    )


__all__ = ["UsbSyncResult", "synchronize_usb_reader"]
