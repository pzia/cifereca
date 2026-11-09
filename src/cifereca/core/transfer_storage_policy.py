"""Storage policy for an explicitly authorized inter-identity local copy.

The transfer storage class is not a bibliographic provider, a supplier account,
a right, a license or a synchronizable source. A recipient never inherits a
donor's supplier directory by receiving independent bytes.

Historical transfers under shared/ or supplier folders remain readable and in place.
No migration is triggered by this policy.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from unicodedata import category


TransferStorageClass = Literal["transfer"]

# #4496 (supersedes Q025): new A→B copies go to Acquisitions.
# Historical shared/ and supplier placements stay valid and unmoved.
TRANSFER_STORAGE_CLASS: TransferStorageClass = "transfer"
_TRANSFER_DIRECTORY = {"transfer": "Acquisitions"}
# Leave room below Linux's 255-byte component limit for the collision suffix.
_TRANSFER_FILENAME_BYTE_LIMIT = 224


def directory_for_storage_class(storage_class: TransferStorageClass) -> str:
    """A closed Core policy, independent of untrusted supplier identifiers."""
    if type(storage_class) is not str or storage_class not in _TRANSFER_DIRECTORY:
        raise ValueError("unsupported storage class")
    return _TRANSFER_DIRECTORY[storage_class]


def _utf8_prefix(value: str, limit: int) -> str:
    return value.encode("utf-8")[:limit].decode("utf-8", "ignore").rstrip(" .-")


def _filename_component(value: object) -> str:
    if type(value) is not str:
        return ""
    value = value.encode("utf-8", "ignore").decode("utf-8")
    # Bidirectional overrides and invisible format controls can disguise
    # extensions or make two readable filenames misleadingly identical.
    value = "".join(" " if category(char) == "Cf" else char for char in value)
    value = re.sub(r"[\x00-\x1f\x7f/\\]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .-")
    return value[:96].rstrip(" .-")


def transfer_readable_filename(
    source_path: Path, *, title: object = None, author: object = None,
) -> str:
    """Name a *new* received copy, not a provider right or existing Placement.

    Only owner-scoped catalogue metadata is admissible. Unknown fields retain
    the readable source filename. Strip filesystem separators/control chars,
    cap encoded length, never return hidden/path traversal names. Collision handling
    and exclusive filesystem publication remain the caller's responsibility.
    """
    title_text = _filename_component(title)
    author_text = _filename_component(author)
    suffix = source_path.suffix
    # Suffix is the witnessed *file's* extension, not an inferred codec from
    # an upstream bibliographic Frmt.
    suffix = suffix if len(suffix) <= 16 and re.fullmatch(r"\.[A-Za-z0-9]+", suffix) else ""
    budget = _TRANSFER_FILENAME_BYTE_LIMIT - len(suffix)
    if title_text:
        # Keep title text even when a long multibyte author fills the budget.
        author_budget = max(budget // 2, budget - 3 - len(title_text.encode("utf-8")))
        author_text = _utf8_prefix(author_text, author_budget)
        stem = f"{author_text} - {title_text}" if author_text else title_text
    else:
        stem = _filename_component(source_path.stem) or "Sans titre"
    return _utf8_prefix(stem[:190], budget) + suffix


__all__ = [
    "TransferStorageClass", "TRANSFER_STORAGE_CLASS",
    "directory_for_storage_class", "transfer_readable_filename",
]
