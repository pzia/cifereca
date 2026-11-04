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

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Mapping

from .device_usb_local_source import attest_local_usb_source
from .device_usb_managed_scan import scan_managed_usb
from .device_usb_preview import UsbPreviewRow, preview_usb_delta


_MAX_INTENTS = 1000
_MAX_LOCAL_PLACEMENTS = 30000


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
    return sources


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
        local = (
            _local_sources(
                storage=storage, identity_id=identity_id,
                identity_root=identity_root, intentions=intentions,
            )
            if scan is not None else {}
        )
        # Re-read all owner/target/intent fields after the costly scan/hash;
        # no stale owner may be published after A→B→A or a rebind.
        last = _scope(storage, identity_id, device_id)
        if last is None or (binding, intentions) != last:
            return unknown("scope_changed")
        observed_at = scan.observed_at if scan is not None else None
        rows = preview_usb_delta(
            identity_id=identity_id, binding=binding,
            placements=list(intentions), local=local,
            scan=scan, receipts=None, now=now if now is not None else int(time.time()),
        )
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
