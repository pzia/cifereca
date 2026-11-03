"""Storage policy for an explicitly authorized inter-identity local copy.

The transfer storage class is not a bibliographic provider, a supplier account,
a right, a license or a synchronizable source. A recipient never inherits a
donor's supplier directory by receiving independent bytes.

Historical transfers under supplier folders remain readable and in place.
No migration is triggered by this policy.
"""
from __future__ import annotations

from typing import Literal


TransferStorageClass = Literal["transfer"]

# Q025: one directory under *each recipient identity*, not a global share.
TRANSFER_STORAGE_CLASS: TransferStorageClass = "transfer"
_TRANSFER_DIRECTORY = {"transfer": "shared"}


def directory_for_storage_class(storage_class: TransferStorageClass) -> str:
    """A closed Core policy, independent of untrusted supplier identifiers."""
    if type(storage_class) is not str or storage_class not in _TRANSFER_DIRECTORY:
        raise ValueError("unsupported storage class")
    return _TRANSFER_DIRECTORY[storage_class]


__all__ = [
    "TransferStorageClass", "TRANSFER_STORAGE_CLASS",
    "directory_for_storage_class",
]
