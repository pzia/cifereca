"""Kobo compatibility facade for the provider-scoped Core B relink.

The physical validator, SQL transaction, CAS token and immutable audit
journal live in canonical_relink. Historical entrypoints stay identical.
"""
from .canonical_relink import (
    CanonicalRelinkRejected,
    relink_verified_canonical,
    relink_verified_canonical_kobo,
)

__all__ = [
    "CanonicalRelinkRejected",
    "relink_verified_canonical",
    "relink_verified_canonical_kobo",
]
