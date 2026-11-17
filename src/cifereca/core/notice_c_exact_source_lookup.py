"""#6366: bounded Core-only exact public-source → canonical Notice C lookup.

The full public catalogue source coordinates, not text, ISBN, an image URL
or a borrower occurrence, establish an edition. Read one SQLite snapshot per
worker batch; do not retain an index across transactions or GTK callbacks.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .notice_c_cover_index import (
    ConsolidatedNoticeCover, _PREFIX, _decode, _json, _IDENTIFIER,
    canonical_cover_document_ref, _db_key,
)
from .notice_search_cover_producer import _coordinate_digest

_MAX_INDEXED_C = 4096
_MAX_RESULTS = 32
_MAX_BATCH = 256

PublicSourceCoordinates = tuple[str, str, str, str, str | None]


def _valid_coordinates(value: object) -> bool:
    if type(value) is not tuple or len(value) != 5:
        return False
    provider, record, docbase, medium, revision = value
    return (
        all(type(part) is str and _IDENTIFIER.fullmatch(part) is not None
            and "://" not in part and ".." not in part
            for part in (provider, record, docbase))
        and type(medium) is str and medium in ("digital", "physical", "unknown")
        and (revision is None or (
            type(revision) is str and _IDENTIFIER.fullmatch(revision) is not None
        ))
    )


def exact_public_source_c_many(
    storage: Any, coordinates: list[PublicSourceCoordinates] | tuple[PublicSourceCoordinates, ...],
) -> dict[PublicSourceCoordinates, ConsolidatedNoticeCover]:
    """Resolve at most 256 exact coordinates using a single bounded C scan.

    Results are independent: contradictory relations or too many claims for
    one coordinate veto THAT coordinate, not the other exact editions.
    Invalid/absent sources are omitted. No IO when there are no valid keys;
    exceeding the scan budget fails closed for the entire batch.
    """
    if type(coordinates) not in (list, tuple) or len(coordinates) > _MAX_BATCH:
        return {}
    wanted = {
        key: "edition:" + _coordinate_digest(
            key[0], "public_catalog", key[1], key[2], key[3], key[4],
        )
        for key in coordinates if _valid_coordinates(key)
    }
    if not wanted:
        return {}
    matches: dict[PublicSourceCoordinates, list[ConsolidatedNoticeCover]] = {}
    rejected: set[PublicSourceCoordinates] = set()
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
                or source.public_bibliography is None for source in sources
            ):
                continue
            found = set()
            for source in sources:
                bio = source.public_bibliography
                coords = (source.provider_id, bio.record_id, bio.docbase,
                          bio.medium, bio.source_revision)
                if coords in wanted and wanted[coords] == bio.edition_id:
                    found.add(coords)
            for coords in found:
                if coords in rejected:
                    continue
                entries = matches.setdefault(coords, [])
                entries.append(consolidated)
                if len(entries) > _MAX_RESULTS:
                    rejected.add(coords)
                    matches.pop(coords, None)
        resolved = {}
        for coords, entries in matches.items():
            # A single proven multi-source edition supersedes its own
            # per-source C; competing multi-source claims never rank by size.
            related = [entry for entry in entries if len(entry.sources) > 1]
            if len(related) == 1:
                resolved[coords] = related[0]
            elif not related and len(entries) == 1:
                resolved[coords] = entries[0]
        return resolved
    except (sqlite3.Error, AttributeError, TypeError, ValueError):
        return {}


def exact_public_source_c_batch(storage: Any, coordinates):
    """Resolve a bounded batch from one consistent SQLite read.

    Keys are (provider, record, docbase, medium, revision). No persistent
    cache: subsequent calls observe new committed C revisions.
    """
    from .notice_c_cover_index import _IDENTIFIER

    requested = {}
    for key in coordinates:
        if len(requested) >= 256:
            break
        provider, record, docbase, medium, revision = key
        if (any(type(value) is not str
                or _IDENTIFIER.fullmatch(value) is None
                or "://" in value or ".." in value
                for value in (provider, record, docbase))
                or medium not in ("digital", "physical", "unknown")
                or (revision is not None
                    and (type(revision) is not str
                         or _IDENTIFIER.fullmatch(revision) is None))):
            continue
        requested[key] = "edition:" + _coordinate_digest(
            provider, "public_catalog", record, docbase, medium, revision,
        )
    if not requested:
        return {}
    matches = {key: [] for key in requested}
    try:
        with storage._lock, storage._connect() as db:
            rows = db.execute(
                "SELECT key,value FROM metadata WHERE key>=? AND key<? "
                "ORDER BY key LIMIT ?",
                (_PREFIX, _PREFIX + "\uffff", _MAX_INDEXED_C + 1),
            ).fetchall()
        if len(rows) > _MAX_INDEXED_C:
            return {}
        for key, raw in rows:
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
                or source.public_bibliography is None for source in sources
            ):
                continue
            found = set()
            for source in sources:
                facts = source.public_bibliography
                coords = (source.provider_id, facts.record_id, facts.docbase,
                          facts.medium, facts.source_revision)
                if requested.get(coords) == facts.edition_id:
                    found.add(coords)
            for coords in found:
                if len(matches[coords]) <= _MAX_RESULTS:
                    matches[coords].append(consolidated)
        result = {}
        for coords, candidates in matches.items():
            if not candidates or len(candidates) > _MAX_RESULTS:
                continue
            related = [x for x in candidates if len(x.sources) > 1]
            if len(related) == 1:
                result[coords] = related[0]
            elif not related and len(candidates) == 1:
                result[coords] = candidates[0]
        return result
    except (sqlite3.Error, AttributeError, TypeError, ValueError):
        return {}


def exact_public_source_c(
    storage: Any, *, provider_id: str, record_id: str, docbase: str,
    medium: str, source_revision: str | None,
) -> ConsolidatedNoticeCover | None:
    """Resolve one exact public edition, rejecting conflicting relations."""
    key = (provider_id, record_id, docbase, medium, source_revision)
    try:
        return exact_public_source_c_batch(storage, [key]).get(key)
    except (TypeError, ValueError):
        return None


__all__ = ["exact_public_source_c", "exact_public_source_c_batch"]
