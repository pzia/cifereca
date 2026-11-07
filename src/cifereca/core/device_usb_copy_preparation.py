"""Read-only Core entry point for reviewing one confirmed USB copy."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import time
from typing import Any, Callable

from .device_usb_copy_admission import (
    UsbCopyAdmission, UsbCopyConsent, UsbCopyRightsEvidence,
    review_usb_copy_admission,
)
from .device_usb_copy_provenance import resolve_live_usb_copy_right
from .device_usb_dry_run import _scope, inspect_usb_dry_run
from .device_usb_preview import UsbLocalArtifact, _sha
from .device_usb_reader_formats import declared_usb_reader_formats


def prepare_usb_copy(
    *, storage: Any, identity_id: str, device_id: str, artifact_id: str,
    identity_root: Path, mountpoint: Path,
    consent: UsbCopyConsent | None, rights: UsbCopyRightsEvidence | None,
    policy_verifier: Callable[[UsbCopyRightsEvidence], bool] | None,
    now: int | None = None,
) -> UsbCopyAdmission:
    """Combine the physical dry run, canonical provenance, policy and consent.

    The caller supplies the configured identity root and a trusted independent
    policy verifier. A positive review conveys no file handle, receipt or
    permission to write. The future executor must verify the live operation
    again under its device lock and consume its confirmation.
    """
    def blocked(reason: str) -> UsbCopyAdmission:
        return UsbCopyAdmission("blocked", reason, identity_id, device_id, artifact_id)

    if (any(type(value) is not str or not value
            for value in (identity_id, device_id, artifact_id))
            or (now is not None and (type(now) is not int or now <= 0))):
        raise ValueError("invalid USB copy preparation request")
    if type(consent) is not UsbCopyConsent:
        return blocked("copy_confirmation_required")
    if type(rights) is not UsbCopyRightsEvidence:
        return blocked("copy_rights_unverified")
    if not callable(policy_verifier):
        return blocked("copy_live_rights_resolver_unavailable")

    try:
        original = _scope(storage, identity_id, device_id)
        if original is None:
            return blocked("copy_scope_unverified")
        binding, intentions = original
        fingerprint = binding["metadata"]["usb_fingerprint"]
        intent = next((row for row in intentions if row["artifact_id"] == artifact_id), None)
        artifact = storage.artifact_get(artifact_id)
        digest = artifact.get("sha256") if type(artifact) is dict else None
        if (intent is None or intent.get("desired_state") != "keep"
                or not _sha(digest) or artifact_id != "sha256:" + digest):
            return blocked("copy_scope_unverified")
        if (consent.identity_id != identity_id or consent.device_id != device_id
                or consent.artifact_id != artifact_id
                or consent.usb_fingerprint != fingerprint
                or consent.target_relative_path != intent.get("target_relative_path")
                or consent.operation != "upload"):
            return blocked("copy_confirmation_invalid_or_expired")

        inspected = inspect_usb_dry_run(
            storage=storage, identity_id=identity_id, device_id=device_id,
            artifact_id=artifact_id, identity_root=identity_root,
            mountpoint=mountpoint, now=now,
        )
        if inspected.status != "scanned":
            return blocked(inspected.status)
        if (inspected.scan_attested is not True or inspected.executable is not False
                or type(inspected.observed_at) is not int or len(inspected.rows) != 1):
            return blocked("copy_physical_review_unverified")
        row = inspected.rows[0]
        if (row.artifact_id != artifact_id or row.action != "upload_candidate"
                or row.observed != "missing" or row.executable is not False):
            return blocked(row.reason)

        # An upload candidate came from the verified owner-local SHA and
        # exact-target content review above. It is merely evidence for the
        # separate live policy and consent checks.
        checked_at = int(time.time()) if now is None else now
        result = review_usb_copy_admission(
            identity_id=identity_id, binding=binding, artifact=artifact,
            local=UsbLocalArtifact(identity_id, digest, verified=True),
            selected_format=artifact["media_format"],
            target_relative_path=intent["target_relative_path"],
            reader=declared_usb_reader_formats(
                identity_id=identity_id, device_id=device_id,
                usb_fingerprint=fingerprint, binding=binding,
            ),
            rights=rights, consent=consent, now=checked_at,
            live_rights_resolver=lambda witness: resolve_live_usb_copy_right(
                storage, witness, policy_verifier=policy_verifier, now=checked_at,
            ),
        )
        if result.status != "review":
            return result
        if (_scope(storage, identity_id, device_id) != original
                or storage.artifact_get(artifact_id) != artifact):
            return blocked("copy_scope_changed")
        finished_at = int(time.time()) if now is None else now
        deadlines = [consent.expires_at, rights.valid_until]
        if rights.loan_expires_at is not None:
            deadlines.append(rights.loan_expires_at)
        if (finished_at < checked_at or finished_at >= min(deadlines)
                or not 0 <= finished_at - inspected.observed_at <= 300):
            return blocked("copy_review_expired")
        return result
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, OverflowError):
        return blocked("copy_preparation_unavailable")


__all__ = ["prepare_usb_copy"]
