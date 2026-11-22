"""#6366: Core-only exact public-source → canonical Notice C lookup.

The full public catalogue source coordinates, not text, ISBN, an image URL
or a borrower occurrence, establish an edition. Read one SQLite snapshot per
worker batch; do not retain an index across transactions or GTK callbacks.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .notice_c_cover_index import (
    ConsolidatedNoticeCover, _PREFIX, _decode, _json, _IDENTIFIER,
    canonical_cover_document_ref, _db_key,
)
from .notice_search_cover_producer import _coordinate_digest
from .notice_cover_projection import notice_cover_document_ref
from .notice_source_observation import NoticeSourceKey

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


def _resolve_public_source_c_many(
    storage: Any, coordinates: list[PublicSourceCoordinates] | tuple[PublicSourceCoordinates, ...],
    *, max_batch: int,
) -> dict[PublicSourceCoordinates, ConsolidatedNoticeCover]:
    """Resolve a caller-bounded set of exact coordinates in one SQLite snapshot.

    Results are independent: contradictory relations or too many claims for
    one coordinate veto THAT coordinate, not the other exact editions.
    Invalid/absent sources are omitted. No IO when there are no valid keys.
    Only exact candidate rows cross the SQLite/Python boundary; unrelated C
    rows never consume a requested coordinate's ambiguity budget.
    """
    if type(coordinates) not in (list, tuple) or len(coordinates) > max_batch:
        return {}
    wanted = {}
    source_refs = {}
    for key in coordinates:
        if not _valid_coordinates(key):
            continue
        provider, record, docbase, medium, revision = key
        edition = "edition:" + _coordinate_digest(
            provider, "public_catalog", record, docbase, medium, revision,
        )
        try:
            source = NoticeSourceKey(
                provider_id=provider, visibility="public", identity_id=None,
                credential_id=None, account_id=None,
                branch_id="search.public.catalogue",
                record_id="search:" + _coordinate_digest(
                    provider, "public_catalog", record,
                ),
                edition_id=edition,
                manifestation_role={
                    "digital": "digital", "physical": "print", "unknown": "unknown",
                }[medium],
            )
            source_refs[notice_cover_document_ref(source).source_key] = key
        except (TypeError, ValueError, UnicodeError):
            continue
        wanted[key] = edition
    if not wanted:
        return {}
    matches: dict[PublicSourceCoordinates, list[ConsolidatedNoticeCover]] = {}
    rejected: set[PublicSourceCoordinates] = set()
    try:
        with storage._lock, storage._connect() as db:
            # The metadata prefix remains the authoritative storage. JSON1
            # filters by exact source DocumentRef, not bibliographic text.
            # A malformed JSON row cannot abort lookup of unrelated C.
            cursor = db.execute(
                "SELECT m.key,m.value FROM metadata AS m "
                "WHERE m.key>=? AND m.key<? AND EXISTS ("
                "SELECT 1 FROM json_each(CASE WHEN json_valid(m.value) "
                "THEN m.value ELSE '{}' END, '$.sources') AS src "
                "WHERE src.key IN (SELECT value FROM json_each(?))) "
                "ORDER BY m.key",
                (_PREFIX, _PREFIX + "\uffff", json.dumps(sorted(source_refs))),
            )
            # Keep the Python validator authoritative. The SQL predicate
            # is only a prefilter, never a document or access proof.
            for key, raw in cursor:
                claimed = set()
                try:
                    # Decode only the opaque claimed source keys first.
                    # The canonical decoder remains the positive authority.
                    wire = json.loads(raw)
                    members = wire.get("sources", {})
                    if type(members) is dict:
                        claimed = {source_refs[ref] for ref in members
                                   if ref in source_refs}
                    if not claimed - rejected:
                        continue
                    parsed = _json(raw)
                    # Once a coordinate exceeds its ambiguity budget, do
                    # not repeatedly decode thousands of additional claims
                    # for it. Still decode rows bearing ANY other requested
                    # coordinate; one noisy source cannot starve its sibling.
                    raw_sources = parsed.get("sources")
                    if type(raw_sources) is not dict or not any(
                        ref in source_refs and source_refs[ref] not in rejected
                        for ref in raw_sources
                    ):
                        continue
                    document = canonical_cover_document_ref(parsed["edition_id"])
                    if _db_key(document) != key:
                        raise ValueError("corrupt_canonical_cover")
                    consolidated = _decode(raw, document)
                except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
                    rejected.update(claimed)
                    for coords in claimed:
                        matches.pop(coords, None)
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


def exact_public_source_c_many(
    storage: Any, coordinates: list[PublicSourceCoordinates] | tuple[PublicSourceCoordinates, ...],
) -> dict[PublicSourceCoordinates, ConsolidatedNoticeCover]:
    """Resolve up to 256 exact search/cache coordinates in one snapshot."""
    return _resolve_public_source_c_many(storage, coordinates, max_batch=_MAX_BATCH)


def exact_public_source_c_inventory(
    storage: Any, coordinates: list[PublicSourceCoordinates] | tuple[PublicSourceCoordinates, ...],
) -> dict[PublicSourceCoordinates, ConsolidatedNoticeCover]:
    """Resolve up to 2048 exact borrower inventory coordinates together."""
    return _resolve_public_source_c_many(storage, coordinates, max_batch=2048)


def exact_public_source_c(
    storage: Any, *,
    provider_id: str, record_id: str, docbase: str, medium: str,
    source_revision: str | None,
) -> ConsolidatedNoticeCover | None:
    """Compatibility single-coordinate worker lookup."""
    coords = (provider_id, record_id, docbase, medium, source_revision)
    if not _valid_coordinates(coords):
        return None
    return exact_public_source_c_many(storage, (coords,)).get(coords)


__all__ = ["exact_public_source_c", "exact_public_source_c_many",
           "exact_public_source_c_inventory"]
