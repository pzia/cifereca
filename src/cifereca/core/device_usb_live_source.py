"""Build the trusted Core revalidation callback for a supervised USB copy."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
import stat
import time
from typing import Any, Callable

from .device_usb_content_review import review_usb_content
from .device_usb_content_snapshot import MAX_SNAPSHOT_BYTES, snapshot_local_usb_content
from .device_usb_copy_admission import (
    UsbCopyConsent, UsbCopyRightsEvidence, review_usb_copy_admission,
)
from .device_usb_copy_provenance import resolve_live_usb_copy_right
from .device_usb_file_commit import _file_version
from .device_usb_local_source import _safe_scope, attest_local_usb_source
from .device_usb_mount import LinuxUsbMount, observe_linux_usb_mount
from .device_usb_preview import _relative
from .device_usb_reader_formats import declared_usb_reader_formats


@dataclass(frozen=True, slots=True)
class UsbLiveCopySource:
    mount: LinuxUsbMount
    identity_root: Path
    payload: bytes = field(repr=False)
    revalidate: Callable[[UsbCopyConsent, UsbCopyRightsEvidence], bool] = field(repr=False)


def prepare_usb_live_source(
    *, storage: Any, placement_id: str, mountpoint: Path,
    consent: UsbCopyConsent, rights: UsbCopyRightsEvidence,
    current_profile: Callable[[], tuple[str, Path, int]],
    policy_verifier: Callable[[UsbCopyRightsEvidence], bool] | None,
) -> UsbLiveCopySource | None:
    """Pin an exact source and give the supervisor a fresh, read-only guard.

    The trusted Core caller supplies the selected profile, its generation,
    and the independent rights policy. It separately runs target preparation.
    Neither this value nor a positive revalidation consumes the consent
    or enables writing without the supervisor and its durable journal.
    """
    if (type(consent) is not UsbCopyConsent or type(rights) is not UsbCopyRightsEvidence
            or type(placement_id) is not str or not placement_id
            or not isinstance(mountpoint, Path) or not mountpoint.is_absolute()
            or not callable(current_profile) or not callable(policy_verifier)):
        return None

    def scope() -> tuple[dict, dict, dict, dict] | None:
        binding = storage.device_binding_get(consent.device_id)
        intent = storage.device_placement_get(consent.device_id, consent.artifact_id)
        artifact = storage.artifact_get(consent.artifact_id)
        placement = storage.placement_get(placement_id)
        if not all(type(row) is dict for row in (binding, intent, artifact, placement)):
            return None
        metadata, owner = binding.get("metadata"), intent.get("metadata")
        managed = binding.get("target_directory")
        target = consent.target_relative_path
        if (binding.get("identity_id") != consent.identity_id
                or binding.get("device_id") != consent.device_id
                or binding.get("enabled") is not True
                or type(metadata) is not dict or metadata.get("usb_fingerprint") != consent.usb_fingerprint
                or intent.get("device_id") != consent.device_id
                or intent.get("artifact_id") != consent.artifact_id
                or type(owner) is not dict or owner.get("intent_identity_id") != consent.identity_id
                or intent.get("desired_state") != "keep" or intent.get("target_relative_path") != target
                or not _relative(managed) or not _relative(target) or not target.startswith(managed + "/")
                or artifact.get("artifact_id") != consent.artifact_id
                or artifact.get("sha256") != consent.sha256
                or artifact.get("media_format") != consent.selected_format
                or placement.get("placement_id") != placement_id
                or placement.get("identity_id") != consent.identity_id
                or placement.get("artifact_id") != consent.artifact_id
                or placement.get("state") != "present"):
            return None
        return binding, intent, artifact, placement

    try:
        profile = current_profile()
        if (type(profile) is not tuple or len(profile) != 3
                or profile[0] != consent.identity_id or not isinstance(profile[1], Path)
                or not profile[1].is_absolute()
                or type(profile[2]) is not int or profile[2] < 0):
            return None
        initial = scope()
        if initial is None:
            return None
        binding, _, artifact, placement = initial
        mount = observe_linux_usb_mount(
            identity_id=consent.identity_id, binding=binding, mountpoint=mountpoint,
        )
        if type(mount) is not LinuxUsbMount or (
            mount.identity_id, mount.device_id, mount.usb_fingerprint, mount.mountpoint
        ) != (consent.identity_id, consent.device_id, consent.usb_fingerprint, mountpoint):
            return None

        root = profile[1]
        path_text = placement.get("path")
        if type(path_text) is not str:
            return None
        path = Path(path_text)
        if not _safe_scope(root, path):
            return None
        pinned = path.lstat()
        if not stat.S_ISREG(pinned.st_mode):
            return None
        local = attest_local_usb_source(
            identity_id=consent.identity_id, identity_root=root, artifact=artifact,
            placement=placement, max_bytes=MAX_SNAPSHOT_BYTES,
        )
        if local is None:
            return None
        payload = snapshot_local_usb_content(
            identity_id=consent.identity_id, identity_root=root, artifact=artifact,
            placement=placement, local=local,
        )
        if payload is None or review_usb_content(
            identity_id=consent.identity_id, artifact=artifact, local=local,
            content=payload, selected_format=consent.selected_format, max_bytes=len(payload),
        ).status != "review":
            return None

        def still_current() -> bool:
            if (current_profile() != profile or scope() != initial
                    or not _safe_scope(root, path)):
                return False
            current = path.lstat()
            if not stat.S_ISREG(current.st_mode) or _file_version(current) != _file_version(pinned):
                return False
            observed = observe_linux_usb_mount(
                identity_id=consent.identity_id, binding=binding, mountpoint=mountpoint,
            )
            return type(observed) is LinuxUsbMount and observed.scope_key() == mount.scope_key()

        def database_revision() -> tuple[int, int]:
            # Match the existing USB observation guard. It covers changes
            # from this connection and commits by a different connection.
            with storage._lock, storage._connect() as db:
                return db.execute("PRAGMA data_version").fetchone()[0], db.total_changes

        reader = declared_usb_reader_formats(
            identity_id=consent.identity_id, device_id=consent.device_id,
            usb_fingerprint=consent.usb_fingerprint, binding=binding,
        )

        def revalidate(live_consent: UsbCopyConsent, live_rights: UsbCopyRightsEvidence) -> bool:
            if (type(live_consent) is not UsbCopyConsent or type(live_rights) is not UsbCopyRightsEvidence
                    or live_consent != consent or live_rights != rights):
                return False
            try:
                revision = database_revision()
                if not still_current():
                    return False
                checked_at = int(time.time())
                decision = review_usb_copy_admission(
                    identity_id=consent.identity_id, binding=binding, artifact=artifact, local=local,
                    selected_format=consent.selected_format, target_relative_path=consent.target_relative_path,
                    reader=reader, rights=rights, consent=consent, now=checked_at,
                    live_rights_resolver=lambda witness: resolve_live_usb_copy_right(
                        storage, witness, policy_verifier=policy_verifier, now=checked_at,
                    ),
                )
                if (decision.status != "review" or not still_current()
                        or database_revision() != revision):
                    return False
                expires_at = min(consent.expires_at, rights.valid_until)
                if rights.loan_expires_at is not None:
                    expires_at = min(expires_at, rights.loan_expires_at)
                return checked_at <= int(time.time()) < expires_at
            except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError):
                return False

        return UsbLiveCopySource(mount, root, payload, revalidate) if revalidate(consent, rights) else None
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError):
        return None


__all__ = ["UsbLiveCopySource", "prepare_usb_live_source"]
