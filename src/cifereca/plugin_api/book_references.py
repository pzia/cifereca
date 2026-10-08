"""Stable public bibliographic projection for contextual book plugins.

Only identifiers provided by a source are exported. A title, author or provider
homepage must never become a fabricated canonical bibliographic identifier.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from .models import BookRef


def _isbn(value: object) -> str:
    """Canonicalize only checksum-valid ISBN-10/13 values."""
    if not isinstance(value, str):
        return ""
    digits = re.sub(r"[\s-]+", "", value).upper()
    if len(digits) == 10 and re.fullmatch(r"[0-9]{9}[0-9X]", digits):
        total = sum((10 - i) * int(digit) for i, digit in enumerate(digits[:9]))
        total += 10 if digits[-1] == "X" else int(digits[-1])
        return digits if total % 11 == 0 else ""
    if len(digits) == 13 and digits.isascii() and digits.isdigit():
        total = sum(int(digit) * (1 if i % 2 == 0 else 3) for i, digit in enumerate(digits))
        return digits if total % 10 == 0 else ""
    return ""


def _candidates(*values: object):
    for value in values:
        if isinstance(value, (list, tuple, set)):
            yield from value
        else:
            yield value


def book_ref_from_row(row: Mapping[str, Any]) -> BookRef:
    """Project a Core library/search row into the existing public BookRef.

    Preserve the current book_id precedence for existing contextual actions.
    Identifiers are additive, and invalid/unattested ISBNs are omitted.
    """
    metadata = row.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    search = metadata.get("search_result")
    search = search if isinstance(search, Mapping) else {}

    def first(*values: object) -> str:
        return next((str(v).strip() for v in values if isinstance(v, (str, int)) and str(v).strip()), "")

    book_id = first(row.get("item_id"), row.get("record_id"), row.get("key"),
                    row.get("artifact_id"), row.get("book_id"))
    provider = first(row.get("provider_id"), row.get("source_provider"),
                     search.get("source_provider"))
    title = first(row.get("title"), search.get("title"))
    raw_authors = row.get("authors") or row.get("author") or search.get("author") or ()
    authors = []
    for value in _candidates(raw_authors):
        if not isinstance(value, str):
            continue
        for name in value.split(";"):
            cleaned = name.strip()
            if cleaned and cleaned not in authors:
                authors.append(cleaned)

    identifiers = {"provider": provider} if provider else {}
    item_id = first(row.get("item_id"), row.get("record_id"))
    if provider and item_id:
        identifiers["source_item_id"] = item_id

    root_ids = row.get("identifiers")
    root_ids = root_ids if isinstance(root_ids, Mapping) else {}
    meta_ids = metadata.get("identifiers")
    meta_ids = meta_ids if isinstance(meta_ids, Mapping) else {}
    search_ids = search.get("identifiers")
    search_ids = search_ids if isinstance(search_ids, Mapping) else {}

    for value in _candidates(
        root_ids.get("isbn13"), root_ids.get("isbn"), root_ids.get("isbn10"),
        row.get("isbn13"), row.get("isbn"), row.get("isbn10"),
        meta_ids.get("isbn13"), meta_ids.get("isbn"), meta_ids.get("isbn10"),
        metadata.get("isbn13"), metadata.get("isbn"), metadata.get("isbn10"),
        search_ids.get("isbn13"), search_ids.get("isbn"), search_ids.get("isbn10"),
        search.get("isbn13"), search.get("isbn"), search.get("isbn10"),
    ):
        canonical = _isbn(value)
        if canonical:
            identifiers["isbn"] = canonical
            break

    return BookRef(
        book_id=book_id,
        title=title,
        authors=tuple(authors),
        identifiers=identifiers,
        metadata=dict(metadata),
    )


__all__ = ["book_ref_from_row"]
