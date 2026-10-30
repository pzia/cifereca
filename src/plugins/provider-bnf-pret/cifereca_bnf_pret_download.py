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
    """Stage a validated ACSM within a pinned directory, without overwrites.

    The Core-selected caller can hold a private staging path across remote
    GETs. No path redirection, including a final-component symlink swapped
    between checks, may send this token to a foreign directory. The helper is
    also used by legacy explicit (never implicit) delivery.
    """
    validate_loan_token(payload)
    directory = Path(target_dir)
    stagefd = None
    try:
        if directory.is_symlink():
            raise ReaderHTTPError("download_storage_failed")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise ReaderHTTPError("download_storage_failed")
        stagefd = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        identity = os.fstat(stagefd)

        def same_stage():
            try:
                current = os.stat(directory, follow_symlinks=False)
                return ((current.st_dev, current.st_ino)
                        == (identity.st_dev, identity.st_ino))
            except OSError:
                return False

        if not same_stage():
            raise ReaderHTTPError("download_storage_failed")

        name = "bnf-loan-" + uuid4().hex + ".acsm"
        part_name = "." + uuid4().hex + ".part"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
        fd = os.open(part_name, flags, 0o600, dir_fd=stagefd)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            if not same_stage():
                raise ReaderHTTPError("download_storage_failed")
            # Both names are relative to the original pinned directory.
            # An attacker swapping the pathname cannot redirect the link.
            os.link(part_name, name, src_dir_fd=stagefd,
                    dst_dir_fd=stagefd, follow_symlinks=False)
            if not same_stage():
                os.unlink(name, dir_fd=stagefd)
                raise ReaderHTTPError("download_storage_failed")
            return directory / name
        finally:
            try:
                os.unlink(part_name, dir_fd=stagefd)
            except FileNotFoundError:
                pass
    except OSError:
        raise ReaderHTTPError("download_storage_failed") from None
    finally:
        if stagefd is not None:
            os.close(stagefd)
