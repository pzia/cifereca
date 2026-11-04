"""P1 #2854: one-operation USB *copy* admission review, non-executable.

Neither a local SHA, declared reader formats, a canonical source reference,
an acquisition entitlement nor a DRM-free-looking file grants USB-copy rights.
An independent trusted Core rights resolver must re-attest explicit permission
for this exact source/artifact/profile/volume, separately from the user's
short-lived, scoped confirmation. This review never writes, stores a token,
calls providers, or authorizes a later transfer.

The future executor must revalidate BOTH rights and confirmation under its
exclusive lock, in addition to the physical SHA/mount/target/receipt guards.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .device_usb_preview import UsbLocalArtifact, _relative, _sha
from .device_usb_variant_review import (
    UsbReaderFormatDeclaration, review_usb_variant,
)


_MAX_AGE = 300
_MAX_REF = 512


def _text(value: object) -> bool:
    return (type(value) is str and 1 <= len(value) <= _MAX_REF
            and value.isprintable() and value == value.strip()
            and "\x00" not in value)


@dataclass(frozen=True, slots=True)
class UsbCopyRightsEvidence:
    """Snapshot from an external *positive* rights policy, never user input.

    source_reference_id identifies the exact owner-scoped canonical source.
    policy_ref is evidence of an actual copy permission, distinct from an
    acquisition/download offer, loan state or this Artifact's metadata.
    """

    identity_id: str
    artifact_id: str
    sha256: str
    selected_format: str
    source_reference_id: str
    provider_id: str
    account_id: str
    source_kind: str
    policy_ref: str
    usb_fingerprint: str
    device_id: str
    observed_at: int
    valid_until: int
    loan_expires_at: int | None = None
    personal_usb_copy_permitted: bool = False


@dataclass(frozen=True, slots=True)
class UsbCopyConsent:
    """Ephemeral exact-target user acknowledgement; not a rights witness."""

    identity_id: str
    device_id: str
    usb_fingerprint: str
    artifact_id: str
    sha256: str
    selected_format: str
    target_relative_path: str
    operation_id: str
    confirmed_at: int
    expires_at: int
    operation: str = "upload"


@dataclass(frozen=True, slots=True)
class UsbCopyAdmission:
    status: str
    reason: str
    identity_id: str
    device_id: str
    artifact_id: str
    # An admitted *review* is never an execution capability.
    executable: bool = False


def review_usb_copy_admission(
    *, identity_id: str, binding: Mapping[str, object],
    artifact: Mapping[str, object], local: UsbLocalArtifact | None,
    selected_format: str, target_relative_path: str,
    reader: UsbReaderFormatDeclaration | None,
    rights: UsbCopyRightsEvidence | None,
    consent: UsbCopyConsent | None,
    live_rights_resolver: Callable[[UsbCopyRightsEvidence], UsbCopyRightsEvidence | None] | None,
    now: int,
) -> UsbCopyAdmission:
    """Review exact live copy rights + one-off consent. Never mint a permit.

    The Core-owned resolver must query current **independent** authority:
    exact canonical owner/source provenance and account, provider/source
    terms permitting a personal USB copy, active/expiry and revoked policy.
    This module CANNOT infer those facts from metadata or authenticate an
    arbitrary caller-supplied evidence object. Without a trusted resolver,
    the result is blocked. No current provider supplies that resolver.
    """
    device = binding.get("device_id") if isinstance(binding, Mapping) else None
    artifact_id = (
        artifact.get("artifact_id") if isinstance(artifact, Mapping) else None
    )
    identity = identity_id if _text(identity_id) else ""
    device = device if _text(device) else ""
    artifact_id = artifact_id if _text(artifact_id) else ""

    def blocked(reason: str) -> UsbCopyAdmission:
        return UsbCopyAdmission(
            "blocked", reason, identity, device, artifact_id,
        )

    if type(now) is not int or not now > 0:
        return blocked("copy_clock_unverified")
    variant = review_usb_variant(
        identity_id=identity_id, binding=binding, artifact=artifact,
        local=local, selected_format=selected_format,
        target_relative_path=target_relative_path, reader=reader,
    )
    if variant.executable is not False or variant.status != "review":
        return blocked("copy_variant_unverified")
    digest = artifact.get("sha256")
    metadata = binding.get("metadata")
    uuid = metadata.get("usb_fingerprint") if type(metadata) is dict else None
    if (type(digest) is not str or not _sha(digest)
            or type(uuid) is not str or not _text(uuid)
            or not _relative(target_relative_path)):
        return blocked("copy_scope_unverified")
    if type(rights) is not UsbCopyRightsEvidence:
        return blocked("copy_rights_unverified")
    if (
        rights.identity_id != identity_id or rights.device_id != device
        or rights.usb_fingerprint != uuid
        or rights.artifact_id != artifact_id or rights.sha256 != digest
        or rights.selected_format != selected_format
        or rights.personal_usb_copy_permitted is not True
        or rights.source_kind not in ("purchase", "loan")
        or any(not _text(v) for v in (
            rights.source_reference_id, rights.provider_id,
            rights.policy_ref,
        ))
        # Canonical provider account_id may legitimately be '' for a
        # provider's primary/default account; it is still compared exactly.
        or type(rights.account_id) is not str
        or len(rights.account_id) > _MAX_REF
        or rights.account_id != rights.account_id.strip()
        or not rights.account_id.isprintable()
        or type(rights.observed_at) is not int
        or type(rights.valid_until) is not int
        or not 0 <= now - rights.observed_at <= _MAX_AGE
        or rights.valid_until <= now
        or rights.valid_until > now + _MAX_AGE
        or (rights.source_kind == "loan" and (
            type(rights.loan_expires_at) is not int
            or rights.loan_expires_at <= now
        ))
        or (rights.loan_expires_at is not None and
            (type(rights.loan_expires_at) is not int
             or rights.loan_expires_at <= now))
    ):
        return blocked("copy_rights_invalid_or_expired")
    if type(consent) is not UsbCopyConsent:
        return blocked("copy_confirmation_required")
    if (
        consent.identity_id != identity_id or consent.device_id != device
        or consent.usb_fingerprint != uuid or consent.artifact_id != artifact_id
        or consent.sha256 != digest or consent.selected_format != selected_format
        or consent.target_relative_path != target_relative_path
        or consent.operation != "upload" or not _text(consent.operation_id)
        or type(consent.confirmed_at) is not int
        or type(consent.expires_at) is not int
        or not 0 <= now - consent.confirmed_at <= _MAX_AGE
        or consent.expires_at <= now
        or consent.expires_at > consent.confirmed_at + _MAX_AGE
    ):
        return blocked("copy_confirmation_invalid_or_expired")
    if not callable(live_rights_resolver):
        return blocked("copy_live_rights_resolver_unavailable")
    try:
        # A trusted Core resolver must perform a FRESH verification, not
        # merely return the supplied right. Equality pins all coordinates;
        # provenance and permission authority remain the resolver's duty.
        current = live_rights_resolver(rights)
    except Exception:
        return blocked("copy_live_rights_verification_failed")
    if type(current) is not UsbCopyRightsEvidence or current != rights:
        return blocked("copy_live_rights_not_attested")
    return UsbCopyAdmission(
        "review", "copy_rights_and_consent_attested_for_review_only",
        identity, device, artifact_id,
    )


__all__ = [
    "UsbCopyRightsEvidence", "UsbCopyConsent", "UsbCopyAdmission",
    "review_usb_copy_admission",
]
