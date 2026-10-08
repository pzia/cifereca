"""Match a public BookRef to an attested entry in the active library snapshot.

This is intentionally a strict, local-only lookup: neither title similarity nor
remote provider access is authority to navigate to a different book.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from cifereca.plugin_api.models import BookRef


def library_key_for_book(
    book: BookRef,
    entries: Iterable[Mapping[str, Any]],
    *,
    identity_id: str,
) -> str | None:
    """Return the unique matching library key, or None for absent/ambiguous books.

    Never select a book outside the active identity, even when the artifact
    SHA is shared. A provider/source pair takes precedence over a bare book ID.
    """
    if not isinstance(book, BookRef) or not isinstance(identity_id, str) or not identity_id:
        return None
    requested = str(book.book_id or "").strip()
    identifiers = book.identifiers if isinstance(book.identifiers, Mapping) else {}
    provider = str(identifiers.get("provider") or "").strip()
    source_id = str(identifiers.get("source_item_id") or "").strip()
    if not requested and not (provider and source_id):
        return None

    matches: set[str] = set()
    for row in entries:
        if not isinstance(row, Mapping) or str(row.get("identity_id") or "") != identity_id:
            continue
        key = str(row.get("key") or "").strip()
        if not key:
            continue

        refs = set()
        primary_provider = str(row.get("provider_id") or "").strip()
        primary_id = str(row.get("item_id") or "").strip()
        if primary_provider and primary_id:
            refs.add((primary_provider, primary_id))
        for ref in row.get("source_refs") or ():
            if not isinstance(ref, Mapping):
                continue
            ref_provider = str(ref.get("provider_id") or "").strip()
            ref_id = str(ref.get("item_id") or "").strip()
            if ref_provider and ref_id:
                refs.add((ref_provider, ref_id))

        if provider:
            if source_id and (provider, source_id) not in refs:
                continue
            if not source_id and provider not in {
                primary_provider, *(ref_provider for ref_provider, _ in refs)
            }:
                continue
        elif source_id and source_id not in {ref_id for _, ref_id in refs}:
            continue

        candidates = {
            str(row.get(name) or "").strip()
            for name in ("key", "item_id", "book_id", "artifact_id")
        }
        candidates.update(ref_id for _, ref_id in refs)
        if requested in candidates or (provider and source_id and (provider, source_id) in refs):
            matches.add(key)
            if len(matches) > 1:
                return None

    return next(iter(matches), None)


__all__ = ["library_key_for_book"]
