"""#6366: bounded Core-only exact public-source → canonical Notice C lookup.

A Loan/History source is NOT an edition relation. Consumers must provide the
complete, independently attested *public catalogue* source coordinates; an
upstream loan ID, title, ISBN, thumbnail URL or provider alone is insufficient.
This reader does not infer permissions or cross-profile ownership and must run
on an existing Core worker, never the GTK render path.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .notice_c_cover_index import (
    ConsolidatedNoticeCover, _PREFIX, _decode, _json,
    canonical_cover_document_ref, _db_key,
)
from .notice_search_cover_producer import _coordinate_digest

_MAX_INDEXED_C = 4096
_MAX_RESULTS = 32


def exact_public_source_c(
    storage: Any, *,
    provider_id: str, record_id: str, docbase: str, medium: str,
    source_revision: str | None,
) -> ConsolidatedNoticeCover | None:
    """Find a C only for a fully observed, unambiguous public source edition.

    The source's independent C may coexist with an explicitly persisted
    multi-source C. Exactly ONE foreign multi-source relation may supersede
    the self edition; conflicting foreign claims fall back to the self C or
    fail closed. Never settle contradictory canonical identities by size.
    Fail closed on unbounded indices and corrupt matching canonical records.
    """
    from .notice_c_cover_index import _IDENTIFIER

    if (any(type(value) is not str
            or _IDENTIFIER.fullmatch(value) is None
            or "://" in value or ".." in value
            for value in (provider_id, record_id, docbase))
            or medium not in ("digital", "physical", "unknown")
            or (source_revision is not None
                and (type(source_revision) is not str
                     or _IDENTIFIER.fullmatch(source_revision) is None))):
        return None
    try:
        edition_id = "edition:" + _coordinate_digest(
            provider_id, "public_catalog", record_id, docbase,
            medium, source_revision,
        )
        # Exact coordinates are compared with validated, retained public
        # bibliographies. Do not trust an index key or a supplied C ref alone.
        with storage._lock, storage._connect() as db:
            raw_rows = db.execute(
                "SELECT key,value FROM metadata WHERE key>=? AND key<? "
                "ORDER BY key LIMIT ?",
                (_PREFIX, _PREFIX + "\uffff", _MAX_INDEXED_C + 1),
            ).fetchall()
        if len(raw_rows) > _MAX_INDEXED_C:
            return None
        matches: list[ConsolidatedNoticeCover] = []
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
            if any(
                source.provider_id == provider_id
                and source.public_bibliography.edition_id == edition_id
                and source.public_bibliography.record_id == record_id
                and source.public_bibliography.docbase == docbase
                and source.public_bibliography.medium == medium
                and source.public_bibliography.source_revision == source_revision
                for source in sources
            ):
                matches.append(consolidated)
                if len(matches) > _MAX_RESULTS:
                    return None
        if not matches:
            return None
        # A single public-source C can be superseded only by ONE independently
        # attested multi-source relation. Choosing the numerically strongest
        # of two *contradictory* foreign groups manufactures an edition link:
        # a third provider in one group does not disprove the other group.
        # Match the conservative route used by the C-first Search reader.
        independent = canonical_cover_document_ref(edition_id)
        own = [c for c in matches if c.document == independent]
        foreign = [
            c for c in matches
            if c.document != independent and len(c.sources) >= 2
        ]
        if len(foreign) == 1:
            return foreign[0]
        if len(own) == 1:
            return own[0]
        # Contradictory foreign groups without a verified self edition, or
        # foreign single-source 'relations', cannot bind a canonical cover.
        return None
    except (sqlite3.Error, AttributeError, TypeError, ValueError):
        return None


__all__ = ["exact_public_source_c"]
