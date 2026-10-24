"""Explicit BnF Web loan token delivery, independent of fulfillment and borrowing.

This module never selects a profile, credits a right, makes a POST or decodes
protected EPUB/PDF contents. The originating provider connection is checked by
BnfProviderSession *before* calling these helpers.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
from uuid import uuid4
from xml.etree import ElementTree

from cifereca_bnf_pret_http import ReaderHTTPError

MAX_TOKEN_BYTES = 2 * 1024 * 1024
ADEPT_TOKEN = "{http://ns.adobe.com/adept}fulfillmentToken"
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def exact_loan_id(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ReaderHTTPError("loan_not_observed")
    return value


def validate_loan_token(payload: bytes) -> None:
    """Accept a bounded ACSM candidate, not an Adobe or ownership attestation."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_TOKEN_BYTES:
        raise ReaderHTTPError("invalid_loan_token")
    # ElementTree does not resolve external entities; additionally reject
    # declaration-based and entity-bearing inputs before parsing any XML.
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ReaderHTTPError("invalid_loan_token")
    try:
        root = ElementTree.fromstring(payload)
    except (ElementTree.ParseError, ValueError, TypeError):
        raise ReaderHTTPError("invalid_loan_token") from None
    if root.tag != ADEPT_TOKEN or root.get("fulfillmentType") != "loan":
        raise ReaderHTTPError("invalid_loan_token")


def save_private_loan_token(payload: bytes, target_dir: Path) -> Path:
    """Never overwrite an ACSM, follow a final-component symlink or publish a partial file."""
    validate_loan_token(payload)
    directory = Path(target_dir)
    if directory.is_symlink():
        raise ReaderHTTPError("download_storage_failed")
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise ReaderHTTPError("download_storage_failed")
        file = directory / ("bnf-loan-" + uuid4().hex + ".acsm")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(file, flags, 0o600)
    except OSError:
        raise ReaderHTTPError("download_storage_failed") from None
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        return file
    except OSError:
        try:
            file.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReaderHTTPError("download_storage_failed") from None
