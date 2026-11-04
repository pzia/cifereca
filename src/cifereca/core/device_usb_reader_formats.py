"""P1 #2854: an exact, non-authorizing reader-format declaration adapter.

Reads only already loaded SQLite binding metadata. The formats belong to one
declared volume UUID and identity; this is *not* attestation of hardware,
DRM-free content, source rights, or any USB copy/delete permission.
"""
from __future__ import annotations

from typing import Mapping

from .device_usb_variant_review import (
    UsbReaderFormatDeclaration, _SUFFIX,
)


def declared_usb_reader_formats(
    *, identity_id: str, device_id: str, usb_fingerprint: str,
    binding: Mapping[str, object],
) -> UsbReaderFormatDeclaration | None:
    """Project only the exact, valid reader declaration; fail closed."""
    if (type(identity_id) is not str or not identity_id
            or type(device_id) is not str or not device_id
            or type(usb_fingerprint) is not str
            or not 8 <= len(usb_fingerprint) <= 128
            or any(char not in
                   "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                   "0123456789._:-" for char in usb_fingerprint)
            or not isinstance(binding, Mapping)
            or binding.get("identity_id") != identity_id
            or binding.get("device_id") != device_id):
        return None
    metadata = binding.get("metadata")
    if (type(metadata) is not dict
            or metadata.get("usb_fingerprint") != usb_fingerprint):
        return None
    formats = metadata.get("reader_formats")
    if (type(formats) is not list
            or not 1 <= len(formats) <= len(_SUFFIX)
            or any(type(value) is not str or value not in _SUFFIX
                   for value in formats)
            or len(set(formats)) != len(formats)):
        return None
    return UsbReaderFormatDeclaration(
        identity_id=identity_id, device_id=device_id,
        usb_fingerprint=usb_fingerprint,
        supported_formats=tuple(formats),
    )


__all__ = ["declared_usb_reader_formats"]
