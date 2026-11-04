"""P1 #2854: SHA-bound USB content inspection, strictly non-authorizing.

Review the provided bytes of one previously attested identity-scoped local
artifact. This is not a reader-format declaration, content license, DRM-free
certificate, or approval to copy. Never call it on the GTK thread.

The caller must independently attest the local path again immediately before
any future transfer: bytes supplied here do not pin an inode or device.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Mapping
from zipfile import BadZipFile, ZipFile

from ..artifact_inspection import inspect_artifact_content
from .device_usb_preview import UsbLocalArtifact, _sha
from .device_usb_variant_review import _SUFFIX


_MAX_INSPECTION_BYTES = 64 * 1024 * 1024
_MAX_EXPANDED_BYTES = 256 * 1024 * 1024


def _epub_archive_budget(content: bytes, *, max_bytes: int) -> str | None:
    """Bound ZIP expansion *before* calling the existing CRC/OPF validator.

    A small compressed EPUB may contain a huge inflated member; inspecting
    its CRC via ZipFile.testzip() must not unpack unbounded attacker bytes.
    The per-request ceiling also makes low-budget inspections testable.
    """
    budget = min(_MAX_EXPANDED_BYTES, max_bytes * 8 + 65536)
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > 20000:
                return "content_archive_unbounded"
            total = 0
            for info in infos:
                if info.file_size < 0 or info.file_size > budget:
                    return "content_archive_unbounded"
                total += info.file_size
                if total > budget:
                    return "content_archive_unbounded"
    except (BadZipFile, OSError, OverflowError, ValueError):
        return "content_structure_unverified"
    return None


@dataclass(frozen=True, slots=True)
class UsbContentReview:
    status: str
    reason: str
    observed_format: str | None = None
    # Invariant: inspection is evidence about bytes, NEVER copy permission.
    executable: bool = False


def _blocked(reason: str, observed: str | None = None) -> UsbContentReview:
    return UsbContentReview("blocked", reason, observed)


def review_usb_content(
    *, identity_id: str, artifact: Mapping[str, object],
    local: UsbLocalArtifact | None, content: bytes,
    selected_format: str, max_bytes: int = _MAX_INSPECTION_BYTES,
) -> UsbContentReview:
    """Inspect an exact SHA-bound in-memory file, refusing invalid content.

    A positive structural result means only that a separate, positive
    rights/DRM attestation and explicit per-reader format support are still
    needed. protected=False on an EPUB is not an absolute DRM-free proof;
    the existing PDF inspector correctly returns protected=None.
    """
    if (type(identity_id) is not str or not identity_id
            or type(selected_format) is not str
            or selected_format not in _SUFFIX
            or type(max_bytes) is not int
            or not 1 <= max_bytes <= _MAX_INSPECTION_BYTES):
        return _blocked("content_request_unverified")
    if (not isinstance(artifact, Mapping)
            or type(artifact.get("artifact_id")) is not str
            or not artifact["artifact_id"]
            or type(artifact.get("sha256")) is not str
            or not _sha(artifact["sha256"])
            or type(artifact.get("size")) is not int
            or not 1 <= artifact["size"] <= max_bytes
            or artifact.get("kind") != "file"
            or artifact.get("media_format") != selected_format
            or type(local) is not UsbLocalArtifact
            or local.verified is not True
            or local.identity_id != identity_id
            or local.sha256 != artifact["sha256"]):
        return _blocked("content_source_unverified")
    digest = artifact["sha256"]
    if (artifact["artifact_id"].startswith("sha256:")
            and artifact["artifact_id"] != "sha256:" + digest):
        return _blocked("content_source_unverified")
    if (type(content) is not bytes or len(content) != artifact["size"]
            or len(content) > max_bytes or sha256(content).hexdigest() != digest):
        return _blocked("content_bytes_unverified")

    metadata = artifact.get("metadata")
    if type(metadata) is not dict:
        return _blocked("content_protection_unverified")
    # Optional DRM descriptors come from persisted/imported metadata. A
    # malformed value (including list/dict) must not raise TypeError during
    # read-only review or be silently interpreted as unprotected.
    if any(field in metadata and type(metadata[field]) is not str
           for field in ("drm_status", "drm_scheme")):
        return _blocked("content_protection_unverified")
    if (metadata.get("artifact_protected") is True
            or metadata.get("protected") is True
            or metadata.get("drm_status") in {"encrypted", "protected"}
            or metadata.get("drm_scheme") in {"adept", "lcp", "kdrm"}):
        return _blocked("content_protection_reported")

    # Structural detection is not evidence that a KEPUB is Kobo-enhanced or
    # that a fixed-layout variant has all its reader-specific semantics.
    expected = "pdf" if selected_format == "pdf" else "epub"
    if expected == "epub":
        invalid_archive = _epub_archive_budget(content, max_bytes=max_bytes)
        if invalid_archive is not None:
            return _blocked(invalid_archive)
    inspected = inspect_artifact_content(
        content, expected_format=expected, strict_structure=True,
    )
    if not inspected.valid or inspected.media_format != expected:
        return _blocked("content_structure_unverified", inspected.media_format)
    if inspected.protected is True:
        return _blocked("content_protection_detected", inspected.media_format)
    if selected_format == "pdf":
        return UsbContentReview(
            "review", "pdf_drm_and_transfer_rights_unverified", "pdf",
        )
    if selected_format in {"kepub", "fxl.kepub"}:
        return UsbContentReview(
            "review", "kepub_semantics_drm_and_transfer_rights_unverified",
            "epub",
        )
    return UsbContentReview(
        "review", "epub_drm_and_transfer_rights_unverified", "epub",
    )


__all__ = ["UsbContentReview", "review_usb_content"]
