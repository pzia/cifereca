"""#6366/#2385: bounded SQLite-first public search over admitted Notice C facts.

Read the SAME immutable source observation that owns the N2 cover; never read
the legacy per-profile mirror as documentary authority. This is an initial
exact-source index cursor, not yet a consolidated multi-provider Notice C, an
attestation of full query coverage, or a provider/rights search.

No network, UI, cache mutation, retrieval of private notices, or book/image
association by title, ISBN, URL or physical-cache HIT. The caller executes
this bounded read away from the GTK main thread.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
import time
import unicodedata

from .book_cover_projection import CoverDocumentScope
from .notice_cover_projection import notice_cover_document_ref
from .notice_cover_repository import _PREFIX, _decode
from .notice_search_cover_producer import _coordinate_digest
from .notice_source_observation import NoticeSourceKey
from .public_search_projection import _public_bibliographic_text

_MAX_SCAN = 10000
_MAX_MATCHES = 50
_STALE_AFTER = 86400
_FIELDS = frozenset(("all", "title", "author", "publisher"))
_SCOPES = frozenset(("all", "digital", "physical", "unknown"))
_BIBLIO_FIELDS = frozenset((
    "schema", "record_id", "docbase", "scenario", "source_collection",
    "medium", "revision_id", "title", "author", "format", "publisher",
    "published_at", "description",
))
_COORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")


@dataclass(frozen=True, slots=True)
class PublicNoticeCHit:
    """Non-actionable descriptive record with its exact N2 DocumentRef."""

    document: CoverDocumentScope
    provider_id: str
    record_id: str
    docbase: str
    medium: str
    source_revision: str | None
    title: str
    author: str | None
    publisher: str | None
    media_format: str | None
    description: str | None
    observed_at: int
    last_seen_at: int
    epoch: int
    cover_state: str
    stale: bool
    edition_id: str = ""


@dataclass(frozen=True, slots=True)
class PublicNoticeCPage:
    """A page of known facts; coverage is NEVER a completed zero verdict."""

    hits: tuple[PublicNoticeCHit, ...]
    known_matches: int
    scanned_sources: int
    unindexed_sources: int
    status: str  # "partial" | "budget_exceeded"
    has_more: bool


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _public_observation(
    raw: object, *, key_string: object, now: int,
) -> PublicNoticeCHit | None:
    if type(raw) is not str or type(key_string) is not str:
        return None
    try:
        payload = json.loads(raw)
        if type(payload) is not dict:
            return None
        coords = payload.get("key")
        if type(coords) is not dict:
            return None
        key = NoticeSourceKey(**coords)
        if (key.visibility != "public"
                or key.identity_id is not None
                or key.credential_id is not None
                or key.account_id is not None
                or key.branch_id != "search.public.catalogue"
                or payload.get("parser_id") != "core.search.public-cover"
                or payload.get("parser_version") != "2"):
            return None
        document = notice_cover_document_ref(key)
        if (key_string != _PREFIX + document.source_key[len("notice-c:v1:"):]):
            return None
        persisted = _decode(raw, document)
        parsed = persisted.observation.fields_copy().get("search_result")
        if type(parsed) is not dict:
            return None
        bio = parsed.get("public_bibliography")
        if (type(bio) is not dict or set(bio) != _BIBLIO_FIELDS
                or type(bio["schema"]) is not int or bio["schema"] != 1
                or bio["source_collection"] != "public_catalog"
                or bio["medium"] not in ("digital", "physical", "unknown")):
            return None

        def coord(name: str, optional: bool = False) -> bool:
            value = bio[name]
            return (value is None and optional) or (
                type(value) is str and _COORD.fullmatch(value) is not None
                and "://" not in value and ".." not in value
            )

        if (not all(coord(name) for name in (
                    "record_id", "docbase", "scenario",
                ))
                or not coord("revision_id", optional=True)
                or key.record_id != "search:" + _coordinate_digest(
                    key.provider_id, bio["source_collection"], bio["record_id"],
                )
                or key.edition_id != "edition:" + _coordinate_digest(
                    key.provider_id, bio["source_collection"], bio["record_id"],
                    bio["docbase"], bio["medium"], bio["revision_id"],
                )):
            return None
        if (not _public_bibliographic_text(
                    bio["title"], required=True, limit=512,
            ) or bio["title"].lstrip().casefold().startswith(
                ("/", "\\", "~/", "file:")
            )):
            return None
        for name in ("author", "format", "publisher", "published_at", "description"):
            value = bio[name]
            if (value is not None
                    and (not _public_bibliographic_text(
                        value, limit=4000 if name == "description" else 512,
                    ) or value.lstrip().casefold().startswith(("/", "\\", "~/", "file:")))):
                return None
        last_seen = payload.get("last_seen_at", persisted.observation.observed_at)
        if (type(last_seen) is not int
                or last_seen < persisted.observation.observed_at
                or last_seen > 2**63 - 1):
            return None
        return PublicNoticeCHit(
            document=document, provider_id=key.provider_id,
            record_id=bio["record_id"], docbase=bio["docbase"],
            medium=bio["medium"], source_revision=bio["revision_id"],
            title=bio["title"], author=bio["author"],
            publisher=bio["publisher"], media_format=bio["format"],
            description=bio["description"],
            observed_at=persisted.observation.observed_at,
            last_seen_at=last_seen, epoch=persisted.epoch,
            cover_state=persisted.attribute.state,
            stale=now - last_seen > _STALE_AFTER,
            edition_id=key.edition_id,
        )
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError,
            UnicodeError, RecursionError):
        return None


def _matches_public_c(
    record: PublicNoticeCHit, *, wanted: set[str],
    scope: str, field: str, needle: str,
) -> bool:
    if wanted and record.provider_id not in wanted:
        return False
    if scope != "all" and record.medium != scope:
        return False
    texts = (
        (record.title,) if field == "title" else
        (record.author or "",) if field == "author" else
        (record.publisher or "",) if field == "publisher" else
        (record.title, record.author or "", record.publisher or "",
         record.description or "")
    )
    return any(needle in _fold(text) for text in texts)


def search_public_notice_c(
    storage: object, query: str, *, field: str = "all",
    scope: str = "all", provider_ids: tuple[str, ...] = (),
    page: int = 0, page_size: int = _MAX_MATCHES,
    max_scan: int = _MAX_SCAN, now: int | None = None,
) -> PublicNoticeCPage:
    """Search durable public C source rows without a supplier or account GET.

    This first-stage cursor has an explicit bounded scan budget, never
    masquerades a partial page as a complete supplier inventory and returns
    no cached entitlement. No per-viewer key: 3 reader profiles see the same
    admitted public C. Private observations are discarded before projection.
    """
    if (type(query) is not str or not 2 <= len(query.strip()) <= 160
            or field not in _FIELDS or scope not in _SCOPES
            or type(provider_ids) is not tuple or len(provider_ids) > 128
            or any(type(provider) is not str or not provider
                   for provider in provider_ids)
            or type(page) is not int or not 0 <= page <= 200
            or type(page_size) is not int or not 1 <= page_size <= _MAX_MATCHES
            or type(max_scan) is not int or not 1 <= max_scan <= 30000
            or (now is not None and (type(now) is not int or now < 1))):
        raise ValueError("invalid_notice_c_search_request")
    now = int(time.time()) if now is None else now
    needle = _fold(query.strip())
    wanted = set(provider_ids)
    matches: list[PublicNoticeCHit] = []
    seen_source_refs: set[str] = set()
    scanned = unindexed = 0
    exceeded = False
    # SQLite's metadata PK is indexed; exact-prefix range is a deterministic
    # bounded cursor and must not enumerate the legacy per-profile mirror.
    with storage._lock, storage._connect() as db:
        cursor = db.execute(
            "SELECT key,value FROM metadata "
            "WHERE key>=? AND key<? "
            "AND CASE WHEN json_valid(value) THEN "
            "json_extract(value, '$.key.visibility') ELSE NULL END='public' "
            "ORDER BY key LIMIT ?",
            (_PREFIX, _PREFIX + "\uffff", max_scan + 1),
        )
        for key_string, raw in cursor:
            if scanned >= max_scan:
                exceeded = True
                break
            scanned += 1
            record = _public_observation(raw, key_string=key_string, now=now)
            if record is None:
                # Older cover-only sources are expected; no false 'missing'.
                unindexed += 1
                continue
            seen_source_refs.add(record.document.source_key)
            if _matches_public_c(
                record, wanted=wanted, scope=scope,
                field=field, needle=needle,
            ):
                matches.append(record)

        # Canonical C owns its public descriptive facts after source eviction.
        # Inspect only remaining bounded rows; never scan the per-profile
        # catalogue, perform a supplier GET or manufacture a negative result.
        # Existing source facts win when present; fallback is ONLY for absent
        # source observations, not a second live result copy.
        if scanned < max_scan:
            from .notice_c_cover_index import (
                _PREFIX as _CANON_PREFIX, _json as _canon_json,
                _decode as _decode_canonical, canonical_cover_document_ref,
            )
            cursor = db.execute(
                "SELECT key,value FROM metadata "
                "WHERE key>=? AND key<? ORDER BY key LIMIT ?",
                (_CANON_PREFIX, _CANON_PREFIX + "\uffff", max_scan - scanned + 1),
            )
            canonical_scanned = 0
            for key_string, raw in cursor:
                if canonical_scanned >= max_scan - scanned:
                    exceeded = True
                    break
                canonical_scanned += 1
                try:
                    row = _canon_json(raw)
                    ref = canonical_cover_document_ref(row["edition_id"])
                    if key_string != _CANON_PREFIX + ref.source_key.split(":")[-1]:
                        continue
                    consolidated = _decode_canonical(raw, ref)
                    public_sources = [
                        src for src in consolidated.sources
                        if src.public_bibliography is not None
                    ]
                    # A source-specific bibliography cannot silently decide
                    # the canonical text when two providers disagree.
                    if len(public_sources) != 1:
                        continue
                    src = public_sources[0]
                    if src.source_ref in seen_source_refs:
                        continue
                    bib = src.public_bibliography
                    hit = PublicNoticeCHit(
                        document=ref, provider_id=src.provider_id,
                        record_id=bib.record_id, docbase=bib.docbase,
                        medium=bib.medium,
                        source_revision=bib.source_revision,
                        title=bib.title, author=bib.author,
                        publisher=bib.publisher,
                        media_format=bib.media_format,
                        description=bib.description,
                        observed_at=src.observed_at,
                        last_seen_at=bib.last_seen_at,
                        epoch=consolidated.epoch,
                        cover_state=src.state,
                        stale=now - bib.last_seen_at > _STALE_AFTER,
                        edition_id=bib.edition_id,
                    )
                except (TypeError, ValueError, KeyError, AttributeError):
                    # Malformed canonical metadata can never widen public
                    # visibility or turn a missing cover into a rights grant.
                    unindexed += 1
                    continue
                if _matches_public_c(
                    hit, wanted=wanted, scope=scope,
                    field=field, needle=needle,
                ):
                    matches.append(hit)

    # Pagination is over *known* source facts, not a confirmed total.
    matches.sort(key=lambda item: (
        _fold(item.title), item.provider_id, item.document.source_key,
    ))
    offset = page * page_size
    selected = matches[offset:offset + page_size]
    if selected:
        from .notice_c_cover_index import (
            canonical_cover_document_ref, read_consolidated_notice_cover,
        )
        canonical_hits = []
        for hit in selected:
            # Keep each bibliographic C source distinct. In the absence of
            # an installed, vetted type-specific text consolidator, a
            # multi-provider cover relation must NOT silently deduplicate
            # differently authored descriptive records. Upgrade N2 only
            # when the canonical index contains exactly THIS source.
            try:
                canonical_ref = canonical_cover_document_ref(hit.edition_id)
                stored = read_consolidated_notice_cover(
                    storage, canonical_ref,
                )
                if (stored is not None and len(stored.sources) == 1
                        and stored.sources[0].source_ref == hit.document.source_key
                        and stored.sources[0].source_epoch == hit.epoch
                        and stored.sources[0].visibility == "public"
                        and (
                            (stored.sources[0].state == "available"
                             and bool(stored.candidates))
                            or (stored.sources[0].state == "pending"
                                and hit.cover_state == "pending"
                                and not stored.candidates)
                        )):
                    hit = replace(
                        hit, document=canonical_ref, epoch=stored.epoch,
                    )
            except (ValueError, TypeError, AttributeError):
                # A corrupt/unrelated canonical index cannot remove a valid
                # exact-source C hit or reassign its bibliography.
                pass
            canonical_hits.append(hit)
        selected = canonical_hits
    return PublicNoticeCPage(
        hits=tuple(selected),
        known_matches=len(matches), scanned_sources=scanned,
        unindexed_sources=unindexed,
        status="budget_exceeded" if exceeded else "partial",
        has_more=exceeded or offset + page_size < len(matches),
    )


__all__ = [
    "PublicNoticeCHit", "PublicNoticeCPage", "search_public_notice_c",
]
