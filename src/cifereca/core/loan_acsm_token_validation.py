"""Provider-neutral local validation of loan fulfillment tokens.

A provider's HTTP response is not evidence of an admissible loan token.
This bounded read is deliberately passive; it never grants a loan, starts
ADEPT or resolves remote links. Exact scope/consent must be checked by the
caller under its own lease and lock.
"""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

MAX_LOAN_TOKEN_BYTES = 2 * 1024 * 1024


def valid_loan_acsm_token(path: Path, inspect_artifact) -> bool:
    """Only a validated Adobe loan fulfillmentToken is a local candidate."""
    inspection = inspect_artifact(path)
    if inspection.valid is not True or inspection.media_format != "acsm":
        return False
    with path.open("rb") as handle:
        content = handle.read(MAX_LOAN_TOKEN_BYTES + 1)
    if not 0 < len(content) <= MAX_LOAN_TOKEN_BYTES:
        return False
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        return False
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, ValueError, TypeError):
        return False
    return (
        root.tag == "{http://ns.adobe.com/adept}fulfillmentToken"
        and root.attrib.get("fulfillmentType") == "loan"
    )



def staged_loan_acsm_path_safe(path: object, destination: object) -> bool:
    """A private ACSM must be a direct, non-aliased child of the exact stage.

    Resolving two paths to the same directory is not sufficient: a provider
    could return an alias through a symlinked ancestor, including the
    grandparent of the staging directory. This check grants no loan/right.
    """
    try:
        if (not isinstance(path, Path) or not isinstance(destination, Path)
                or path.parent != destination or path.suffix.casefold() != ".acsm"
                or path.is_symlink() or not path.is_file()
                or not destination.is_dir()):
            return False
        current = destination
        while True:
            if current.is_symlink():
                return False
            if current == current.parent:
                break
            current = current.parent
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def discard_untrusted_staged_acsm(path: object, destination: Path) -> None:
    """Unlink only a candidate directly inside the caller's staging dir.

    Never follow a symlink or delete an arbitrary provider-returned path.
    """
    try:
        if staged_loan_acsm_path_safe(path, destination):
            path.unlink(missing_ok=True)
    except (OSError, ValueError, RuntimeError):
        pass
