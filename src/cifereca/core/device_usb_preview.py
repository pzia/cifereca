"""P1 #2854: pure, read-only USB device sync delta preview.

Inputs are snapshots obtained by the caller, NOT an authorization to write.
A later executor must revalidate device fingerprint, owned source SHA, exact
destination, managed receipt and user confirmation under an exclusive lock.
No USB I/O, network, filesystem traversal or storage mutation occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class UsbLocalArtifact:
    identity_id: str
    sha256: str
    verified: bool = False


@dataclass(frozen=True, slots=True)
class UsbObservedFile:
    relative_path: str
    sha256: str
    # A symlink/unknown object must never be treated as a managed book.
    regular_file: bool = False


@dataclass(frozen=True, slots=True)
class UsbManagedReceipt:
    """Proof of an earlier completed Cifereca write, not a placement hint."""

    identity_id: str
    device_id: str
    fingerprint: str
    relative_path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class UsbDeviceScan:
    """Caller-supplied bounded observation of the managed device area only."""

    identity_id: str
    device_id: str
    fingerprint: str
    observed_at: int
    complete: bool
    files: Mapping[str, UsbObservedFile] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UsbPreviewRow:
    artifact_id: str
    desired: str
    observed: str
    last_known: str
    last_observed_at: int | None
    action: str
    reason: str
    # A preview is never an execution capability, including removal candidates.
    executable: bool = False


def _sha(value: object) -> bool:
    return (
        type(value) is str and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _relative(path: object) -> bool:
    """Only a simple managed relative path; never infer safety from a name."""
    if type(path) is not str or not 1 <= len(path) <= 512:
        return False
    return (
        not path.startswith("/")
        and "\\" not in path and ":" not in path
        and all(part not in {"", ".", ".."} for part in path.split("/"))
        and all(part.isprintable() for part in path.split("/"))
    )


def _safe_scanned_files(files: object) -> bool:
    """Require a coherent, one-to-one regular-file inventory for absence."""
    if not isinstance(files, Mapping) or len(files) > 100000:
        return False
    paths: set[str] = set()
    for artifact_id, observed in files.items():
        if (type(artifact_id) is not str or not artifact_id
                or type(observed) is not UsbObservedFile
                or observed.regular_file is not True
                or not _relative(observed.relative_path)
                or not _sha(observed.sha256)):
            return False
        path_key = observed.relative_path.casefold()
        if path_key in paths:
            # Duplicate or case-fold-colliding target paths are ambiguous.
            # Do not infer that an absent ID is physically missing.
            return False
        paths.add(path_key)
    return True


def _fresh_scan(
    scan: UsbDeviceScan | None, *,
    identity_id: str, device_id: str, fingerprint: object,
    now: object, max_age_seconds: int,
) -> bool:
    # The mount directory name alone is not a USB device identity.
    return (
        type(scan) is UsbDeviceScan
        and type(fingerprint) is str and 1 <= len(fingerprint) <= 128
        and scan.fingerprint == fingerprint
        and scan.device_id == device_id and scan.identity_id == identity_id
        and type(scan.complete) is bool and scan.complete
        and type(scan.observed_at) is int and scan.observed_at > 0
        and type(now) is int and scan.observed_at <= now
        and now - scan.observed_at <= max_age_seconds
        and _safe_scanned_files(scan.files)
    )


def preview_usb_delta(
    *, identity_id: str, binding: Mapping[str, object],
    placements: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
    local: Mapping[str, UsbLocalArtifact],
    scan: UsbDeviceScan | None = None,
    receipts: Mapping[str, UsbManagedReceipt] | None = None,
    now: int | None = None,
    max_age_seconds: int = 300,
) -> tuple[UsbPreviewRow, ...]:
    """Classify scoped intentions; NEVER copy, delete or make a fresh claim.

    A stale persisted `device_placements.observed_state=present/missing`
    is retained only as `last_known`. It cannot establish current presence
    or absence. Only a complete, recent, fingerprint-bound scan can do that.

    `upload_candidate` and `remove_candidate` are advisory: *both* require
    execution-time validation. Removal additionally requires a prior exact
    managed-write receipt, not merely a matching filename or stored placement.
    """
    if (type(identity_id) is not str or not identity_id
            or not isinstance(binding, Mapping)
            or type(binding.get("device_id")) is not str
            or not binding["device_id"]
            or binding.get("identity_id") != identity_id):
        raise ValueError("device binding is outside selected identity")
    if (type(placements) not in (tuple, list) or len(placements) > 100000
            or not isinstance(local, Mapping)
            or (receipts is not None and not isinstance(receipts, Mapping))
            or type(max_age_seconds) is not int
            or not 1 <= max_age_seconds <= 3600):
        raise ValueError("invalid device preview inputs")
    device_id = binding["device_id"]
    metadata = binding.get("metadata")
    fingerprint = metadata.get("usb_fingerprint") if type(metadata) is dict else None
    fresh = _fresh_scan(
        scan, identity_id=identity_id, device_id=device_id,
        fingerprint=fingerprint, now=now, max_age_seconds=max_age_seconds,
    )
    enabled = binding.get("enabled") is True
    result: list[UsbPreviewRow] = []
    seen: set[str] = set()
    for placement in placements:
        if (not isinstance(placement, Mapping)
                or placement.get("device_id") != device_id
                or type(placement.get("artifact_id")) is not str
                or not placement["artifact_id"]
                or placement["artifact_id"] in seen):
            # A cross-device/corrupt row must not be exposed by this view.
            raise ValueError("invalid or cross-device placement")
        # device_placements historically has no identity column. A device
        # binding can be re-assigned without clearing old intentions, so its
        # current owner is NOT evidence that each row belongs to that owner.
        # Require the intent's original exact identity in the row metadata;
        # fail closed rather than show a previous profile's artifact ID.
        intent_meta = placement.get("metadata")
        if (type(intent_meta) is not dict
                or intent_meta.get("intent_identity_id") != identity_id):
            raise ValueError("unproven placement owner")
        artifact_id = placement["artifact_id"]
        seen.add(artifact_id)
        desired = placement.get("desired_state")
        last = placement.get("observed_state")
        known = last if last in ("present", "missing") and type(last) is str else "unknown"
        ts = placement.get("last_observed_at")
        ts = ts if type(ts) is int and ts > 0 else None
        current = "unknown"
        action, reason = "verify", "device_observation_required"
        if not enabled:
            action, reason = "blocked", "device_disabled_or_unverified"
        elif desired not in ("keep", "remove") or type(desired) is not str:
            action, reason = "blocked", "invalid_intent"
        elif fresh:
            # A complete scan can prove absence only at a *defined* target.
            # New declarative keep intentions intentionally start with no
            # path: they must first be mapped to a device-compatible variant
            # and a unique managed location, not silently counted as missing.
            target = placement.get("target_relative_path")
            managed_dir = binding.get("target_directory")
            scoped = (
                _relative(target)
                and (managed_dir is None or (
                    type(managed_dir) is str
                    and _relative(managed_dir)
                    and target.startswith(managed_dir.rstrip("/") + "/")
                ))
            )
            observed = scan.files.get(artifact_id)
            occupied = (
                _relative(target) and any(
                    key != artifact_id
                    and entry.relative_path.casefold() == target.casefold()
                    for key, entry in scan.files.items()
                )
            )
            if not scoped:
                action, reason = "verify", "target_path_unqualified"
            elif occupied:
                # A foreign/untracked file at this path cannot become an
                # implicit candidate for overwrite or removal.
                action, reason = "blocked", "target_path_occupied"
            elif observed is not None and observed.relative_path != target:
                action, reason = "blocked", "observed_target_path_mismatch"
            else:
                current = "present" if observed is not None else "missing"
                if desired == "remove":
                    if observed is None:
                        action, reason = "skip", "already_missing"
                    else:
                        receipt = (receipts or {}).get(artifact_id)
                        matching = (
                            type(receipt) is UsbManagedReceipt
                            and receipt.identity_id == identity_id
                            and receipt.device_id == device_id
                            and receipt.fingerprint == fingerprint
                            and receipt.relative_path == observed.relative_path
                            and receipt.sha256 == observed.sha256
                        )
                        action, reason = (
                            ("remove_candidate", "managed_removal_requires_confirmation")
                            if matching else ("blocked", "unmanaged_or_changed_file")
                        )
                elif observed is not None:
                    source = local.get(artifact_id)
                    match = (
                        type(source) is UsbLocalArtifact and source.verified is True
                        and source.identity_id == identity_id
                        and _sha(source.sha256) and source.sha256 == observed.sha256
                    )
                    action, reason = (
                        ("skip", "identical_sha_observed")
                        if match else ("blocked", "source_or_device_sha_unverified")
                    )
                else:
                    source = local.get(artifact_id)
                    verified = (
                        type(source) is UsbLocalArtifact and source.verified is True
                        and source.identity_id == identity_id and _sha(source.sha256)
                    )
                    action, reason = (
                        ("upload_candidate", "revalidate_before_usb_write")
                        if verified else ("blocked", "owned_source_unverified")
                    )
        result.append(UsbPreviewRow(
            artifact_id=artifact_id,
            desired=desired if type(desired) is str and desired in ("keep", "remove") else "unknown",
            observed=current, last_known=known, last_observed_at=ts,
            action=action, reason=reason,
        ))
    return tuple(result)


__all__ = [
    "UsbLocalArtifact", "UsbObservedFile", "UsbManagedReceipt",
    "UsbDeviceScan", "UsbPreviewRow", "preview_usb_delta",
]
