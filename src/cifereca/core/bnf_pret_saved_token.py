"""BnF-Prêt P0 #2055 — passive validation of a voluntarily saved .bin file.

This does not implement a provider, borrower matching, HTTP, DRM, ADEPT
fulfillment, rights verification or a copy/rename operation. Content and
path remain private; only closed evidence codes can cross the Core boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
from xml.etree import ElementTree


ADEPT_NS = "http://ns.adobe.com/adept"
DC_NS = "http://purl.org/dc/elements/1.1/"
MAX_SAVED_TOKEN_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SavedTokenEvidence:
    """No XML text, path, account, loan ID or raw expiry reaches the caller."""
    code: str
    media_format: str | None = None
    expiry_known: bool = False


def inspect_saved_bnf_token(path: Path, *, now: datetime | None = None) -> SavedTokenEvidence:
    """Read one explicitly supplied user-local file, without network or writes.

    No extension or Content-Type inference; only ADEPT namespaced XML is a
    candidate. An ACSM candidate is not proof of entitlement, a valid HMAC,
    active server loan, completed ADEPT exchange or materialized EPUB/PDF.
    """
    invalid = SavedTokenEvidence("file_unavailable")
    fd = None
    try:
        if not isinstance(path, Path) or path.is_symlink():
            return invalid
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1):
            return invalid
        if metadata.st_size <= 0:
            return SavedTokenEvidence("invalid_xml")
        if metadata.st_size > MAX_SAVED_TOKEN_BYTES:
            return SavedTokenEvidence("token_too_large")
        with os.fdopen(fd, "rb", closefd=False) as source:
            raw = source.read(MAX_SAVED_TOKEN_BYTES + 1)
        if len(raw) > MAX_SAVED_TOKEN_BYTES:
            return SavedTokenEvidence("token_too_large")
        if len(raw) != metadata.st_size:
            return SavedTokenEvidence("file_unavailable")
    except (OSError, ValueError, TypeError):
        return invalid
    finally:
        if fd is not None:
            os.close(fd)

    # Decode before checking forbidden markup, including case-insensitive
    # mixed-case declarations. Only UTF-8(-BOM) is accepted; UTF-16 and
    # alternate encodings must not bypass the DTD/ENTITY ban.
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return SavedTokenEvidence("invalid_xml")
    lowered = decoded.casefold()
    if "<!doctype" in lowered or "<!entity" in lowered:
        return SavedTokenEvidence("unsafe_xml")
    try:
        root = ElementTree.fromstring(decoded)
    except (ValueError, ElementTree.ParseError):
        return SavedTokenEvidence("invalid_xml")
    if root.tag != f"{{{ADEPT_NS}}}fulfillmentToken":
        return SavedTokenEvidence("not_acsm")

    format_elem = root.find(
        f"{{{ADEPT_NS}}}resourceItemInfo/"
        f"{{{ADEPT_NS}}}metadata/"
        f"{{{DC_NS}}}format"
    )
    declared = (format_elem.text or "").strip().casefold() if format_elem is not None else ""
    media_format = (
        "pdf" if declared == "application/pdf" else
        "epub" if declared in {"application/epub+zip", "application/epub"} else
        None
    )

    expires = root.find(f"{{{ADEPT_NS}}}expiration")
    if expires is None or not (expires.text or "").strip():
        return SavedTokenEvidence("candidate_acsm", media_format, False)
    expiry = (expires.text or "").strip()
    try:
        if len(expiry) > 50:
            raise ValueError("too long")
        when = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("timezone missing")
        reference = now if now is not None else datetime.now(timezone.utc)
        if not isinstance(reference, datetime) or reference.utcoffset() is None:
            raise ValueError("reference time must be aware")
        expired = when <= reference
    except (ValueError, OverflowError, TypeError):
        return SavedTokenEvidence("invalid_expiry", media_format, False)
    return SavedTokenEvidence(
        "token_expired" if expired else "candidate_acsm", media_format, True,
    )
