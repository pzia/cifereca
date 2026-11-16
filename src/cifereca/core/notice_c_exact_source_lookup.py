"""#6366: bounded worker-only exact public-source -> canonical Notice C lookup.

A Loan/History source is NOT an edition relation. Consumers must supply the
complete, independently attested public catalogue coordinates. A title, ISBN,
cover URL, byte hash, upstream loan ID or source provider alone is insufficient.
The batch reader scans the bounded durable C index once per ready Search batch,
never once per GTK row. It does not infer permissions or cross-profile access.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .notice_c_cover_index import (
    ConsolidatedNoticeCover, _PREFIX, _decode, _json,
    canonical_cover_document_ref, _db_key, _IDENTIFIER,
)
from .notice_search_cover_producer import _coordinate_digest

_MAX_INDEXED_C = 4096
_MAX_RESULTS = 32
_MAX_REQUESTED = 64

PublicSourceCoordinates = tuple[str, str, str, str, str | None]


def _qualified(coordinates: object) -> bool:
    if type(coordinates) is not tuple or len(coordinates) != 5:
        return False
    provider_id, record_id, docbase, medium, revision = coordinates
    return (
        all(type(value) is str
            and _IDENTIFIER.fullmatch(value) is not None
            and "://" not in value and ".." not in value
            for value in (provider_id, record_id, docbase))
        and type(medium) is str
        and medium in ("digital", "physical", "unknown")
        and (revision is None or (
            type(revision) is str
            and _IDENTIFIER.fullmatch(revision) is not None
        ))
    )


def exact_public_sources_c(
    storage: Any, coordinates: tuple[PublicSourceCoordinates, ...],
) -> dict[PublicSourceCoordinates, ConsolidatedNoticeCover]:
    """Resolve many complete public catalogue sources with one SQLite scan.

    An own-source C is safe by itself. A *sole* multi-public foreign C may
    supersede it if the original provider source is a verified member.
    Contradictory foreign C groups fall back to own C or unknown: choosing
    the numerically larger group would invent an edition relationship.
    """
    if (type(coordinates) is not tuple
            or not 1 <= len(coordinates) <= _MAX_REQUESTED
            or not all(_qualified(value) for value in coordinates)):
        return {}
    wanted = set(coordinates)
    edition_ids = {
        key: "edition:" + _coordinate_digest(
            key[0], "public_catalog", key[1], key[2], key[3], key[4],
        )
        for key in wanted
    }
    found: dict[
        PublicSourceCoordinates, list[ConsolidatedNoticeCover]
    ] = {key: [] for key in wanted}
    overflow: set[PublicSourceCoordinates] = set()
    try:
        with storage._lock, storage._connect() as db:
            raw_rows = db.execute(
                "SELECT key,value FROM metadata WHERE key>=? AND key<? "
                "ORDER BY key LIMIT ?",
                (_PREFIX, _PREFIX + "\uffff", _MAX_INDEXED_C + 1),
            ).fetchall()
        if len(raw_rows) > _MAX_INDEXED_C:
            return {}
        for key, raw in raw_rows:
            try:
                parsed = _json(raw)
                document = canonical_cover_document_ref(parsed["edition_id"])
                if _db_key(document) != key:
                    continue
                consolidated = _decode(raw, document)
            except (KeyError, TypeError, ValueError):
                continue
            sources = consolidated.sources
            if not sources or any(
                source.visibility != "public"
                or source.public_bibliography is None
                for source in sources
            ):
                continue
            matched: set[PublicSourceCoordinates] = set()
            for source in sources:
                bib = source.public_bibliography
                source_key = (
                    source.provider_id, bib.record_id, bib.docbase,
                    bib.medium, bib.source_revision,
                )
                if (source_key in wanted
                        and bib.edition_id == edition_ids[source_key]):
                    matched.add(source_key)
            for source_key in matched:
                matches = found[source_key]
                if len(matches) >= _MAX_RESULTS:
                    overflow.add(source_key)
                else:
                    matches.append(consolidated)
        selected: dict[PublicSourceCoordinates, ConsolidatedNoticeCover] = {}
        for source_key, matches in found.items():
            if source_key in overflow or not matches:
                continue
            own_ref = canonical_cover_document_ref(edition_ids[source_key])
            own = [item for item in matches if item.document == own_ref]
            foreign = [item for item in matches if item.document != own_ref]
            if len(foreign) == 1 and len(foreign[0].sources) >= 2:
                selected[source_key] = foreign[0]
            elif len(own) == 1:
                selected[source_key] = own[0]
        return selected
    except (sqlite3.Error, AttributeError, TypeError, ValueError):
        return {}


def exact_public_source_c(
    storage: Any, *,
    provider_id: str, record_id: str, docbase: str, medium: str,
    source_revision: str | None,
) -> ConsolidatedNoticeCover | None:
    """Resolve one fully observed source, including after short-lived TTL."""
    key = (provider_id, record_id, docbase, medium, source_revision)
    return exact_public_sources_c(storage, (key,)).get(key)


__all__ = [
    "PublicSourceCoordinates", "exact_public_source_c",
    "exact_public_sources_c",
]
