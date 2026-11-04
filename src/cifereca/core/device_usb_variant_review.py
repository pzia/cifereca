"""P1 #2854: non-authorizing USB book-variant/reader-format review.

This is an advisory filter for a previously SHA-attested local file. A
reader's declared formats and an artifact's filename/media_format do not prove
content compatibility, a DRM-free file, or transfer rights. In particular a
plain EPUB, Kobo KEPUB or fixed-layout KEPUB is never auto-converted or
renamed into another variant. An executor must separately prove permissions,
inspect actual content, and recheck source, target and device under lock.

No filesystem, network, SQLite, USB or GTK work occurs in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .device_usb_preview import UsbLocalArtifact, _relative, _sha


# Distinct file variants: these suffixes are NOT interchangeable, even though
# all three EPUB variants have the same outer ZIP/MIME family.
_SUFFIX = {
    "epub": ".epub",
    "kepub": ".kepub.epub",
    "fxl.kepub": ".fxl.kepub.epub",
    "pdf": ".pdf",
}


@dataclass(frozen=True, slots=True)
class UsbReaderFormatDeclaration:
    """Formats declared for one reader, not independently verified hardware."""

    identity_id: str
    device_id: str
    usb_fingerprint: str
    supported_formats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UsbVariantReview:
    status: str
    reason: str
    # Deliberately impossible to use as a copy/delete authorization token.
    executable: bool = False


def _refuse(reason: str) -> UsbVariantReview:
    return UsbVariantReview("blocked", reason)


def review_usb_variant(
    *, identity_id: str, binding: Mapping[str, object],
    artifact: Mapping[str, object], local: UsbLocalArtifact | None,
    selected_format: str, target_relative_path: str | None,
    reader: UsbReaderFormatDeclaration | None,
) -> UsbVariantReview:
    """Qualify only static file-format compatibility, never copy permission.

    A positive review means *only* that independent DRM/rights/content and
    hardware checks are still required. Neither artifact_protected=False
    nor a recognized suffix is proof that the EPUB/PDF can be delivered.
    """
    if (type(identity_id) is not str or not identity_id
            or not isinstance(binding, Mapping)
            or binding.get("identity_id") != identity_id
            or type(binding.get("device_id")) is not str
            or not binding["device_id"]
            or binding.get("enabled") is not True):
        return _refuse("reader_scope_unverified")
    device_id = binding["device_id"]
    metadata = binding.get("metadata")
    fingerprint = metadata.get("usb_fingerprint") if type(metadata) is dict else None
    if (type(fingerprint) is not str or not 8 <= len(fingerprint) <= 128
            or any(char not in "abcdefghijklmnopqrstuvwxyz"
                   "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
                   for char in fingerprint)):
        return _refuse("reader_scope_unverified")
    if (not isinstance(artifact, Mapping)
            or type(artifact.get("artifact_id")) is not str
            or not artifact["artifact_id"]
            or type(artifact.get("sha256")) is not str
            or not _sha(artifact["sha256"])
            or type(artifact.get("size")) is not int
            or artifact["size"] <= 0
            or artifact.get("kind") != "file"
            or type(local) is not UsbLocalArtifact
            or local.verified is not True
            or local.identity_id != identity_id
            or local.sha256 != artifact["sha256"]):
        return _refuse("owned_source_unverified")
    if artifact["artifact_id"].startswith("sha256:") and (
            artifact["artifact_id"] != "sha256:" + artifact["sha256"]):
        return _refuse("owned_source_unverified")
    fmt = artifact.get("media_format")
    if (type(selected_format) is not str or selected_format not in _SUFFIX
            or type(fmt) is not str or fmt != selected_format):
        return _refuse("selected_variant_unverified")
    if (type(reader) is not UsbReaderFormatDeclaration
            or reader.identity_id != identity_id
            or reader.device_id != device_id
            or type(reader.usb_fingerprint) is not str
            or reader.usb_fingerprint != fingerprint
            or type(reader.supported_formats) is not tuple
            or not 1 <= len(reader.supported_formats) <= len(_SUFFIX)
            or any(type(value) is not str or value not in _SUFFIX
                   for value in reader.supported_formats)
            or len(set(reader.supported_formats)) != len(reader.supported_formats)):
        return _refuse("reader_formats_unconfigured")
    if selected_format not in reader.supported_formats:
        return _refuse("reader_format_unsupported")
    managed = binding.get("target_directory")
    if (not _relative(managed) or not _relative(target_relative_path)
            or not target_relative_path.startswith(managed + "/")):
        return _refuse("target_path_unqualified")
    # Avoid accepting 'novel.kepub.epub' as a plain EPUB or fixed-layout as
    # reflowable KEPUB merely because the last suffix also matches.
    suffix = _SUFFIX[selected_format]
    name = target_relative_path.rsplit("/", 1)[-1]
    if (not name.endswith(suffix) or len(name) <= len(suffix)
            or (selected_format == "epub"
                and name.endswith((".kepub.epub", ".fxl.kepub.epub")))
            or (selected_format == "kepub" and name.endswith(".fxl.kepub.epub"))):
        return _refuse("target_variant_suffix_mismatch")
    artifact_metadata = artifact.get("metadata")
    if type(artifact_metadata) is dict and (
            artifact_metadata.get("artifact_protected") is True
            or artifact_metadata.get("protected") is True
            or artifact_metadata.get("drm_status") in ("encrypted", "protected")
            or artifact_metadata.get("drm_scheme") in ("adept", "lcp", "kdrm")):
        return _refuse("protection_reported")
    # Metadata cannot establish DRM-free state or transfer permissions.
    return UsbVariantReview("review", "drm_and_transfer_rights_unverified")


__all__ = [
    "UsbReaderFormatDeclaration", "UsbVariantReview", "review_usb_variant",
]
