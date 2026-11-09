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


def discard_untrusted_staged_acsm(path: object, destination: Path) -> None:
    """Unlink only a candidate directly inside the caller's staging dir.

    Never follow a symlink or delete an arbitrary provider-returned path.
    """
    if not isinstance(path, Path):
        return
    try:
        if path.suffix.casefold() != ".acsm":
            return
        if (not destination.is_symlink() and not path.is_symlink()
                and path.parent.resolve() == destination.resolve()
                and path.is_file()):
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass
