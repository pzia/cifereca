"""Read-only boundary between a canonical Kobo source and the 0.7 direct GET.

The v0.7 backup key has no borrower or branch. Any typed SourceReference
for the same credential/edition prevents legacy direct acquisition, even
when the provider session has an incomplete delivery ABI.
"""
from __future__ import annotations


def has_typed_kobo_source(storage, credential_id: str, edition_id: str) -> bool:
    """Do not confer legacy authority where Core already has an exact source."""
    if (type(credential_id) is not str or not credential_id
            or type(edition_id) is not str or not edition_id):
        return False
    with storage._lock, storage._connect() as db:
        return db.execute(
            "SELECT 1 FROM source_references WHERE provider_id='kobo' "
            "AND credential_id=? AND edition_id=? LIMIT 1",
            (credential_id, edition_id),
        ).fetchone() is not None
