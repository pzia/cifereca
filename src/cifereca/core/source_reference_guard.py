"""Provider-neutral read-only fence for a typed canonical source.

SourceReference identity is scoped by provider/credential/edition. This guard
only detects that an independent canonical source already exists; it never
attests a right, delivers bytes, or grants legacy acquisition permission.
"""
from __future__ import annotations


def has_typed_source(
    storage, provider_id: str, credential_id: str, edition_id: str,
) -> bool:
    """Protect a typed source against parallel legacy writers of any provider."""
    if any(type(value) is not str or not value
           for value in (provider_id, credential_id, edition_id)):
        return False
    with storage._lock, storage._connect() as db:
        return db.execute(
            "SELECT 1 FROM source_references "
            "WHERE provider_id=? AND credential_id=? AND edition_id=? LIMIT 1",
            (provider_id, credential_id, edition_id),
        ).fetchone() is not None
