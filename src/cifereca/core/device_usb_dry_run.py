"""P1 #2854: explicitly requested, non-executable USB dry-run coordinator.

This Core boundary combines existing exact-UUID Linux scan, owned local SHA
attestation and pure delta projection. It must be invoked in a worker after
the user asks to examine a reader: never from page construction, navigation,
book-row rendering or a synchronous GTK callback.

No USB writing, deletion, mount, provider request, entitlement, receipt or
SQLite mutation. A dry-run is not an execution token. Any future executor
must independently re-attest profile, book, UUID, target, rights and receipt
under its own exclusive lock and a separate explicit confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import time
from typing import Any, Mapping

from .device_usb_local_source import attest_local_usb_source
from .device_usb_content_snapshot import (
    MAX_RUN_SNAPSHOT_BYTES, MAX_SNAPSHOT_BYTES,
    snapshot_local_usb_content,
)
from .device_usb_content_review import review_usb_content
from .device_usb_managed_scan import scan_managed_usb
from .device_usb_preview import UsbPreviewRow, _fresh_scan, preview_usb_delta
from .device_usb_reader_formats import declared_usb_reader_formats
from .device_usb_variant_review import review_usb_variant
from .device_usb_target_preflight import (
    UsbTargetPreflight, preflight_usb_target,
)


_MAX_INTENTS = 1000
_MAX_LOCAL_PLACEMENTS = 30000
_MAX_TARGET_PREFLIGHTS = 64
_USB_FREE_SPACE_RESERVE = 4 * 1024 * 1024
# Advisory per-item physical checks are deliberately bounded because a volume
# might contain thousands of books. Full absence was already scanned once.


@dataclass(frozen=True, slots=True)
class UsbDryRun:
    """Advisory observation only; never authority for upload/removal."""

    identity_id: str
    device_id: str
    status: str
    scan_attested: bool
    observed_at: int | None
    rows: tuple[UsbPreviewRow, ...]
    executable: bool = False


def _scope(storage: Any, identity_id: str, device_id: str):
    """Read one complete, owner-attested SQLite snapshot or refuse it."""
    binding = storage.device_binding_get(device_id)
    if (type(binding) is not dict
            or binding.get("identity_id") != identity_id
            or binding.get("device_id") != device_id
            or type(binding.get("metadata")) is not dict
            or type(binding["metadata"].get("usb_fingerprint")) is not str):
        return None
    fingerprint = binding["metadata"]["usb_fingerprint"]
    if (not 8 <= len(fingerprint) <= 128
            or any(ch not in "abcdefghijklmnopqrstuvwxyz"
                   "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
                   for ch in fingerprint)):
        return None
    placements = storage.device_placements(device_id=device_id)
    if type(placements) is not list or len(placements) > _MAX_INTENTS:
        return None
    seen: set[str] = set()
    for row in placements:
        if (type(row) is not dict
                or row.get("device_id") != device_id
                or type(row.get("artifact_id")) is not str
                or not row["artifact_id"]
                or row["artifact_id"] in seen
                or type(row.get("metadata")) is not dict
                or row["metadata"].get("intent_identity_id") != identity_id):
            return None
        seen.add(row["artifact_id"])
    return binding, tuple(placements)


def _local_sources(
    *, storage: Any, identity_id: str,
    identity_root: Path, intentions: tuple[Mapping[str, object], ...],
) -> dict[str, object]:
    """Verify only exact owned placement bytes; reject ambiguous duplicate paths.

    A missing or invalid source is *not* evidence of rights or a failed GET.
    It yields no local positive SHA. This may be expensive: worker only.
    """
    rows = storage.placements(state="present")
    if type(rows) is not list or len(rows) > _MAX_LOCAL_PLACEMENTS:
        raise ValueError("unbounded local placement snapshot")
    requested = {r["artifact_id"] for r in intentions
                 if r.get("desired_state") == "keep"}
    matched: dict[str, list[Mapping[str, object]]] = {
        artifact_id: [] for artifact_id in requested
    }
    for row in rows:
        if (type(row) is not dict
                or row.get("identity_id") != identity_id
                or row.get("artifact_id") not in matched):
            continue
        matched[row["artifact_id"]].append(row)
    sources = {}
    artifacts = {}
    owned_placements = {}
    for artifact_id, candidates in matched.items():
        if len(candidates) != 1:
            continue
        artifact = storage.artifact_get(artifact_id)
        if type(artifact) is not dict or artifact.get("artifact_id") != artifact_id:
            continue
        proof = attest_local_usb_source(
            identity_id=identity_id, identity_root=identity_root,
            artifact=artifact, placement=candidates[0],
        )
        if proof is not None:
            sources[artifact_id] = proof
            artifacts[artifact_id] = artifact
            owned_placements[artifact_id] = candidates[0]
    return sources, artifacts, owned_placements


def inspect_usb_dry_run(
    *, storage: Any, identity_id: str, device_id: str,
    identity_root: Path, mountpoint: Path, now: int | None = None,
) -> UsbDryRun:
    """Inspect an explicitly selected reader, fail closed on any scope change.

    This function never determines whether the user may transfer or remove
    any book. In particular, it supplies **no** managed-write receipts:
    historic observed_state=present and the target filename cannot authorize
    a deletion, including when the current scanned bytes match the source.
    """
    if (type(identity_id) is not str or not identity_id
            or type(device_id) is not str or not device_id
            or not isinstance(identity_root, Path)
            or not identity_root.is_absolute()
            or not isinstance(mountpoint, Path)
            or not mountpoint.is_absolute()
            or type(now) not in (int, type(None))
            or (now is not None and now <= 0)):
        raise ValueError("invalid USB dry-run request")
    unknown = lambda status: UsbDryRun(
        identity_id=identity_id, device_id=device_id,
        status=status, scan_attested=False,
        observed_at=None, rows=(),
    )
    try:
        first = _scope(storage, identity_id, device_id)
        if first is None:
            return unknown("scope_unverified")
        binding, intentions = first
        if not intentions:
            return unknown("no_intentions")

        # Never attempt to scan a disabled device. The scan's mount guard
        # also revalidates block UUID and USB sysfs lineage before/after.
        scan = (
            scan_managed_usb(
                identity_id=identity_id, binding=binding,
                mountpoint=mountpoint, placements=intentions,
            )
            if binding.get("enabled") is True else None
        )
        # No complete USB evidence means no current presence/absence can be
        # inferred; avoid expensive local hashing for an unplugged reader.
        check_time = now if now is not None else int(time.time())
        # The scanner is a separate, independently implemented producer.
        # Do not label an incomplete/stale/foreign inventory as "scanned"
        # merely because it returned a non-None object.
        metadata = binding.get("metadata")
        fingerprint = metadata.get("usb_fingerprint") if type(metadata) is dict else None
        if not _fresh_scan(
            scan, identity_id=identity_id, device_id=device_id,
            fingerprint=fingerprint, now=check_time, max_age_seconds=300,
        ):
            scan = None
        local, artifacts, owned_placements = (
            _local_sources(
                storage=storage, identity_id=identity_id,
                identity_root=identity_root, intentions=intentions,
            )
            if scan is not None else ({}, {}, {})
        )
        # Re-read owner/target/intent fields after the costly scan/hash.
        # Changed final snapshots are refused; byte-identical A→B→A
        # cannot be proven by this advisory check and will require a
        # durable generation + fresh exclusive-lock proof in the executor.
        last = _scope(storage, identity_id, device_id)
        if last is None or (binding, intentions) != last:
            return unknown("scope_changed")
        observed_at = scan.observed_at if scan is not None else None
        rows = preview_usb_delta(
            identity_id=identity_id, binding=binding,
            placements=list(intentions), local=local,
            scan=scan, receipts=None, now=check_time,
        )
        # A verified local SHA and a missing USB target do not establish
        # reader compatibility. Use only the stored *declaration* and exact
        # variant. No content parsing, DRM judgement or write authority.
        reader = declared_usb_reader_formats(
            identity_id=identity_id, device_id=device_id,
            usb_fingerprint=fingerprint, binding=binding,
        )
        by_artifact = {intent["artifact_id"]: intent for intent in intentions}
        qualified = []
        content_bytes_used = 0
        preflight_count = 0
        planned_bytes = 0
        for row in rows:
            if row.action != "upload_candidate":
                qualified.append(row)
                continue
            if reader is None:
                qualified.append(replace(
                    row, action="verify", reason="reader_formats_unconfigured",
                ))
                continue
            artifact = artifacts.get(row.artifact_id)
            intent = by_artifact[row.artifact_id]
            if type(artifact) is not dict:
                return unknown("scope_unverified")
            review = review_usb_variant(
                identity_id=identity_id, binding=binding, artifact=artifact,
                local=local.get(row.artifact_id),
                selected_format=artifact.get("media_format"),
                target_relative_path=intent.get("target_relative_path"),
                reader=reader,
            )
            if review.executable is not False:
                return unknown("scope_unverified")
            if review.status == "blocked":
                qualified.append(replace(
                    row, action="blocked", reason=review.reason,
                ))
            elif review.status == "review":
                # Static reader declarations and artifact extensions alone
                # cannot certify the bytes. Take a second bounded physical
                # snapshot under owner/path/inode/SHA checks; never rely on
                # historical path names or this advisory result for a write.
                size = artifact.get("size")
                if type(size) is not int or size <= 0:
                    qualified.append(replace(
                        row, action="blocked", reason="content_source_unverified",
                    ))
                    continue
                if size > MAX_SNAPSHOT_BYTES:
                    qualified.append(replace(
                        row, action="verify", reason="content_inspection_size_limit",
                    ))
                    continue
                if content_bytes_used + size > MAX_RUN_SNAPSHOT_BYTES:
                    qualified.append(replace(
                        row, action="verify", reason="content_inspection_budget_exhausted",
                    ))
                    continue
                if preflight_count >= _MAX_TARGET_PREFLIGHTS:
                    qualified.append(replace(
                        row, action="verify",
                        reason="target_preflight_budget_exhausted",
                    ))
                    continue
                preflight_count += 1
                preflight = preflight_usb_target(
                    identity_id=identity_id, binding=binding,
                    mountpoint=mountpoint, artifact=artifact,
                    intention=intent,
                )
                if (type(preflight) is not UsbTargetPreflight
                        or preflight.executable is not False):
                    return unknown("scope_unverified")
                if (preflight.status == "blocked"
                        and preflight.reason in {
                            "target_mount_unverified", "target_mount_changed",
                            "target_scope_unverified", "target_directory_changed",
                            "target_directory_unverified", "target_io_unverified",
                        }):
                    # If the volume/directory lost attestation AFTER the
                    # complete scan, even a previously observed "missing"
                    # must not be described as current. Drop the whole view.
                    return unknown("scope_unverified")
                if preflight.status == "blocked":
                    # Physical absence/space are advisory, never grant rights.
                    # Unavailable mount/IO = verify, not false absence.
                    serious = {
                        "target_occupied", "target_space_insufficient",
                        "target_volume_read_only",
                    }
                    qualified.append(replace(
                        row, action="blocked" if preflight.reason in serious
                        else "verify", reason=preflight.reason,
                    ))
                    continue
                if (preflight.status != "review"
                        or preflight.reason != "target_absent_space_observed"
                        or type(preflight.available_bytes) is not int
                        or preflight.available_bytes < 0):
                    return unknown("scope_unverified")
                if (planned_bytes + size + _USB_FREE_SPACE_RESERVE
                        > preflight.available_bytes):
                    qualified.append(replace(
                        row, action="blocked",
                        reason="target_batch_capacity_insufficient",
                    ))
                    continue
                content_bytes_used += size
                content = snapshot_local_usb_content(
                    identity_id=identity_id, identity_root=identity_root,
                    artifact=artifact, placement=owned_placements[row.artifact_id],
                    local=local[row.artifact_id],
                )
                if content is None:
                    qualified.append(replace(
                        row, action="verify", reason="content_bytes_unverified",
                    ))
                    continue
                structural = review_usb_content(
                    identity_id=identity_id, artifact=artifact,
                    local=local[row.artifact_id], content=content,
                    selected_format=artifact.get("media_format"),
                    # Budget the ZIP's possible decompression against the
                    # *actual* compressed size, not a fixed 64 MiB maximum.
                    # The full run already bounds the sum of these sizes.
                    max_bytes=size,
                )
                if structural.executable is not False:
                    return unknown("scope_unverified")
                if structural.status == "blocked":
                    qualified.append(replace(
                        row, action="blocked", reason=structural.reason,
                    ))
                elif structural.status == "review":
                    planned_bytes += size
                    qualified.append(replace(
                        row, reason=structural.reason,
                    ))
                else:
                    return unknown("scope_unverified")
            else:
                return unknown("scope_unverified")
        # The content pass may be long enough for the user to change
        # identity/reader or revoke formats while the worker is running.
        # An advisory preview from the previous snapshot must be discarded.
        latest = _scope(storage, identity_id, device_id)
        if latest is None or latest != (binding, intentions):
            return unknown("scope_changed")
        rows = tuple(qualified)
        if any(row.executable is not False for row in rows):
            return unknown("scope_unverified")
        return UsbDryRun(
            identity_id=identity_id,
            device_id=device_id,
            status="scanned" if scan is not None else "device_unverified",
            scan_attested=scan is not None,
            observed_at=observed_at, rows=rows,
        )
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return unknown("scope_unverified")


__all__ = ["UsbDryRun", "inspect_usb_dry_run"]
