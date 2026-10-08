"""Stable public bibliographic projection for contextual book plugins.

Only identifiers provided by a source are exported. A title, author or provider
homepage must never become a fabricated canonical bibliographic identifier.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
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




@dataclass(frozen=True, slots=True)
class BookAccessFact:
    """An access claim and the exact class of evidence supporting it.

    "local" means a file is present, not that it is legally owned; "borrowed"
    means an active loan, not that another reader can currently borrow it.
    """

    kind: Literal["local", "owned", "borrowed", "borrowable", "free"]
    evidence: str


def book_access_facts(
    row: Mapping[str, Any], *, identity_id: str | None = None,
) -> tuple[BookAccessFact, ...]:
    """Project only *explicit* access evidence from public catalogue/library rows.

    No inferred rights from a provider name, an HTTP download link, a generic
    availability label or a loan previously seen in another identity. Unknown
    access is represented by an empty tuple rather than a fabricated status.
    """
    if not isinstance(row, Mapping):
        return ()
    identities = row.get("identity_ids")
    if identity_id and isinstance(identities, (tuple, list)) and identities:
        if identity_id not in identities:
            return ()
    elif identity_id and row.get("identity_id") and row["identity_id"] != identity_id:
        return ()

    facts: list[BookAccessFact] = []

    def add(kind: Literal["local", "owned", "borrowed", "borrowable", "free"], evidence: str) -> None:
        fact = BookAccessFact(kind, evidence)
        if fact not in facts:
            facts.append(fact)

    if row.get("local") is True:
        if not identity_id:
            add("local", "library.local")
        else:
            placements = row.get("placements")
            if isinstance(placements, (list, tuple)) and placements:
                # A merged book may have one local copy and several remote
                # rights belonging to other identities. Only a present
                # placement proves local access for the requested identity.
                if any(
                    isinstance(p, Mapping)
                    and p.get("identity_id") == identity_id
                    and p.get("state") == "present"
                    for p in placements
                ):
                    add("local", "placement.present")
            elif row.get("identity_id"):
                # Legacy single-placement rows predate the public placement
                # list but still attest one specific identity.
                if row.get("identity_id") == identity_id:
                    add("local", "library.local")
            elif isinstance(identities, (list, tuple)):
                # Single-identity legacy rows may have identity_ids without
                # identity_id. Multiple identities are ambiguous: fail closed.
                scoped_ids = {value for value in identities if isinstance(value, str) and value}
                if scoped_ids == {identity_id}:
                    add("local", "library.local")

    possessions = row.get("possessions")
    if isinstance(possessions, (tuple, list)):
        for entry in possessions:
            if not isinstance(entry, Mapping) or entry.get("state") != "active":
                continue
            if identity_id and entry.get("identity_id") != identity_id:
                continue
            if entry.get("kind") == "owned":
                add("owned", "entitlement.owned")
            elif entry.get("kind") == "loan":
                add("borrowed", "entitlement.loan")

    metadata = row.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    search = metadata.get("search_result")
    search = search if isinstance(search, Mapping) else {}
    for source, name in ((row, "provider"), (metadata, "metadata"), (search, "search_result")):
        # Identity-scoped possession is not proven by an unscoped catalogue hit.
        if source.get("owned") is True and (not identity_id or row.get("identity_id") == identity_id):
            add("owned", name + ".owned")
        if source.get("borrowable") is True or source.get("available_to_borrow") is True:
            add("borrowable", name + ".borrowable")
        if source.get("free") is True:
            add("free", name + ".free")
    return tuple(facts)


__all__ = ["book_ref_from_row", "BookAccessFact", "book_access_facts"]
