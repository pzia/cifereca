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
from .device_usb_preview import UsbPreviewRow, _sha
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
    artifact_id: str | None = None,
    exact_delta: bool = False,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> UsbSyncResult:
    """Apply only exact per-book confirmations from a fresh advisory delta.

    No confirmation means pending, not an implicit authorization. A scan
    failure never becomes an empty delta or a successfully synced reader.
    An exact SHA scope limits a single-book confirmation without changing
    the historical full-reader mode (artifact_id=None).
    """
    if (not isinstance(identity_root, Path) or not identity_root.is_absolute()
            or not isinstance(mountpoint, Path) or not mountpoint.is_absolute()
            or not isinstance(lock_root, Path) or not lock_root.is_absolute()
            or type(identity_id) is not str or not identity_id
            or type(device_id) is not str or not device_id
            or not callable(current_profile)
            or not isinstance(confirmations, Mapping)
            or not isinstance(rights, Mapping)
            or (artifact_id is not None and (
                type(artifact_id) is not str
                or not artifact_id.startswith("sha256:")
                or not _sha(artifact_id[7:])
            ))):
        return UsbSyncResult("blocked")

    preview = inspect_usb_dry_run(
        storage=storage, identity_id=identity_id, device_id=device_id,
        identity_root=identity_root, mountpoint=mountpoint,
        artifact_id=artifact_id,
    )
    if (preview.status != "scanned" or preview.scan_attested is not True
            or preview.executable is not False
            or (artifact_id is not None and (
                type(preview.rows) is not tuple or len(preview.rows) != 1
                or type(preview.rows[0]) is not UsbPreviewRow
                or preview.rows[0].artifact_id != artifact_id
            ))):
        return UsbSyncResult("unavailable")

    if exact_delta:
        if (type(preview.rows) is not tuple
                or any(type(row) is not UsbPreviewRow or row.executable is not False
                       for row in preview.rows)):
            return UsbSyncResult("unavailable")
        candidates = {
            row.artifact_id: (UsbCopyConsent if row.action == "upload_candidate"
                              else UsbRemovalConsent)
            for row in preview.rows
            if row.action in ("upload_candidate", "remove_candidate")
        }
        if (set(confirmations) != set(candidates)
                or any(type(confirmations[key]) is not cls
                       for key, cls in candidates.items())):
            return UsbSyncResult("blocked")
    journal = SqliteUsbOperationJournal(storage)
    placement_ids = {}
    if any(row.action == "upload_candidate" for row in preview.rows):
        sources = storage.placements(state="present")
        if type(sources) is not list or len(sources) > 30000:
            return UsbSyncResult("blocked")
        for source in sources:
            if (type(source) is dict and source.get("identity_id") == identity_id
                    and type(source.get("artifact_id")) is str):
                key = source["artifact_id"]
                placement_ids[key] = (
                    None if key in placement_ids else source.get("placement_id")
                )
    if any(row.reason in ("target_batch_capacity_insufficient", "target_space_insufficient")
           for row in preview.rows):
        return UsbSyncResult("blocked")
    copied = removed = pending = failed = 0
    processed = copied_bytes = 0
    total = sum(row.action in ("upload_candidate", "remove_candidate") for row in preview.rows)
    def notify():
        if progress is not None:
            try:
                progress(processed, total, copied_bytes)
            except Exception:
                pass  # Presentation failure is not a storage failure.
    def stopped():
        return cancelled is not None and cancelled()
    def interrupted():
        return UsbSyncResult("cancelled", copied=copied, removed=removed,
                             pending=pending + total - processed, failed=failed)
    notify()
    for row in preview.rows:
        if stopped():
            return interrupted()
        if check_cancelled is not None:
            check_cancelled()
        if row.action not in ("upload_candidate", "remove_candidate"):
            if row.action not in ("none", "up_to_date", "noop", "skip"):
                pending += 1
            continue
        consent = confirmations.get(row.artifact_id)
        if row.action == "upload_candidate":
            authorization = rights.get(row.artifact_id)
            placement_id = placement_ids.get(row.artifact_id)
            if (type(consent) is not UsbCopyConsent
                    or consent.artifact_id != row.artifact_id
                    or consent.identity_id != identity_id or consent.device_id != device_id
                    or (authorization is not None and type(authorization) is not UsbCopyRightsEvidence)
                    or (authorization is None and policy_verifier is not None)
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
                if progress is not None:
                    try:
                        artifact = storage.artifact_get(row.artifact_id)
                        if type(artifact) is dict and type(artifact.get("size")) is int:
                            copied_bytes += artifact["size"]
                    except Exception:
                        pass  # Telemetry cannot invalidate a committed copy.
            else:
                failed += 1
                if result.status == "recovery_required":
                    break
        else:
            if (type(consent) is not UsbRemovalConsent
                    or consent.artifact_id != row.artifact_id
                    or consent.identity_id != identity_id or consent.device_id != device_id):
                pending += 1
                continue
            def removal_profile() -> tuple[str, int]:
                profile = current_profile()
                return profile[0], profile[2]

            result = execute_usb_removal(
                storage=storage, copy_journal=journal, removal_journal=journal,
                lock_root=lock_root, mountpoint=mountpoint, consent=consent,
                current_profile=removal_profile,
                check_cancelled=check_cancelled,
            )
            if result.status == "removed":
                removed += 1
            else:
                failed += 1
                if result.status == "recovery_required":
                    break

        processed += 1
        notify()
    if stopped():
        return interrupted()
    # An earlier Core removal review may have refused an operation because
    # the originating GTK selection was revoked *during* inode hashing.
    # That refusal is not permission to persist a fresh observation under a
    # now-stale reader selection. Recheck before any SQLite observation write.
    if check_cancelled is not None:
        check_cancelled()
    # Even an all-noop delta updates the canonical observed timestamp after
    # a successful physical scan; a copy receipt alone is not presence.
    verification = record_verified_usb_observation(
        storage=storage, identity_id=identity_id, device_id=device_id,
        mountpoint=mountpoint,
    )
    observed = verification.status == "recorded"
    if not observed:
        failed += 1
    return UsbSyncResult(
        "partial" if failed else "pending" if pending else "completed",
        copied=copied, removed=removed, pending=pending,
        failed=failed, observed=observed,
    )


__all__ = ["UsbSyncResult", "synchronize_usb_reader"]
