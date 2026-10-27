"""Conservative offline fallback for a failed *public* provider search (#1916).

Only mirrored public catalogue rows of the active identity may be returned.
This is not a purchase, loan, holding, download or fresh provider result.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from ..core.search import filter_loaded_results
from ..models import SearchResult
from ..search_failures import search_failure_code

_MAX_CATALOG_ROWS = 10000
_MAX_RESULTS = 50
_MAX_AGE_SECONDS = 90 * 86400
_PUBLIC_COLLECTIONS = frozenset({"catalog", "public", "public_catalog"})
_PUBLIC_META = frozenset({
    "subjects", "subject", "series", "collection", "publisher", "languages",
    "language", "isbn", "isbns", "authors", "contributors", "genre",
    "genres", "categories", "medium", "description", "published_at",
})


_PUBLIC_STALE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,510}")


def _safe_stale_identifier(value: Any) -> bool:
    """Legacy SQLite IDs can be URLs, filesystem paths or signed handles.

    Stale public fallback is best-effort. Refuse any record whose opaque
    identifier is not safely representable without those private values.
    """
    return type(value) is str and _PUBLIC_STALE_ID.fullmatch(value) is not None


def _safe_stale_bibliographic_text(value: Any, *, field: str) -> str | None:
    """Never project legacy, previously unvalidated SQLite free-text as public."""
    from .search_orchestration import _safe_public_search_text
    checked = _safe_public_search_text(field, value)
    if type(checked) is not str:
        return None
    # A path is not a bibliographic source identifier. Backslashes are
    # forbidden conservatively, even for rare legitimate book titles.
    if checked.startswith(("/", "~/", "file:", "\\")) or "\\" in checked:
        return None
    if re.match(r"^[A-Za-z]:[/\\]", checked):
        return None
    return checked


def public_service_unavailable(exc: BaseException) -> bool:
    """A remote transport/protocol outage, never an auth or rights refusal."""
    return search_failure_code(exc) == "SearchTemporarilyUnavailable"


@dataclass(frozen=True, slots=True)
class KnownPublicFallbackPage:
    """Public, immutable offline page; a budget failure is never a zero match."""
    results: tuple[SearchResult, ...]
    status: str
    page: int
    page_size: int
    matched_count: int
    has_more: bool


def known_public_fallback_page(
    storage: Any, *, provider_id: str, identity_id: str | None,
    query: str, scope: str, field: str, content_types: tuple[str, ...],
    now: int | None = None, page: int = 0, page_size: int = _MAX_RESULTS,
) -> KnownPublicFallbackPage:
    """Deterministically page already-known public results, without any HTTP.

    An entire provider/identity scope is scanned once up to the existing
    10k cap; the declared matched_count is exact only inside that bounded
    scope. If more rows exist, return *no* results and no false completeness.
    """
    def empty(status: str) -> KnownPublicFallbackPage:
        return KnownPublicFallbackPage(
            (), status, page if type(page) is int and page >= 0 else 0,
            page_size if type(page_size) is int and page_size > 0 else 0,
            0, False,
        )

    if (type(page) is not int or page < 0 or page > _MAX_CATALOG_ROWS
            or type(page_size) is not int or not 1 <= page_size <= _MAX_RESULTS
            or page * page_size >= _MAX_CATALOG_ROWS):
        return empty("invalid_page")
    # A public notice is qualified by its scoped, fresh, catalogue-only
    # evidence below, NEVER by a hard-coded supplier allow/deny-list.
    # The same provider may publish both public notices and private purchases;
    # only public collections without entitlement/placement are eligible.
    if (type(identity_id) is not str or not identity_id
            or type(provider_id) is not str or not provider_id
            or type(query) is not str or len(query.strip()) < 2):
        return empty("unavailable")
    current = int(time.time()) if now is None else int(now)
    try:
        # Apply exact identity/provider scope in SQLite before the read budget.
        # Other users' catalogues cannot starve a small source's local fallback.
        entries = storage.catalog_entries(
            identity_id=identity_id, provider_id=provider_id,
            limit=_MAX_CATALOG_ROWS + 1,
        )
        if len(entries) > _MAX_CATALOG_ROWS:
            return empty("scope_budget_exceeded")
    except Exception:
        return empty("unavailable")
    # The Core mirror already normalizes these bibliographic fields. Perform
    # the same bounded, private-URL rejecting check on persisted rows too:
    # old SQLite rows may predate that validator or have been imported by a
    # different producer, so public stale output cannot trust raw metadata.
    from .search_orchestration import _PUBLIC_SEARCH_TEXT_FIELDS, _safe_public_search_text
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        if (entry.get("provider_id") != provider_id
                or entry.get("identity_id") != identity_id
                or entry.get("local_state") not in {
                    "remote_catalog", "available_to_buy", "available_to_borrow",
                }
                or entry.get("artifact_id") or entry.get("placement_id")):
            continue
        meta = entry.get("metadata")
        if not isinstance(meta, dict):
            continue
        # State/absence of ownership alone cannot attest public visibility.
        # Legacy labels, missing provenance and private collections stay out
        # rather than being relabelled as public by this offline projection.
        collection = meta.get("source_collection")
        if (type(collection) is not str
                or collection.strip().casefold() not in _PUBLIC_COLLECTIONS):
            continue
        mirrored = meta.get("mirrored_at")
        if (type(mirrored) is not int or mirrored > current
                or current - mirrored > _MAX_AGE_SECONDS):
            continue
        item_id = entry.get("item_id")
        catalog_id = entry.get("catalog_entry_id")
        if not (_safe_stale_identifier(item_id)
                and _safe_stale_identifier(catalog_id)):
            continue
        safe_title = _safe_stale_bibliographic_text(entry.get("title"), field="title")
        if safe_title is None:
            continue
        raw_author = entry.get("author")
        safe_author = _safe_stale_bibliographic_text(raw_author, field="author")
        # Discard an untrusted author without discarding an otherwise attested
        # public notice; author-specific queries must not match that value.
        safe_format = entry.get("media_format")
        if (type(safe_format) is not str or len(safe_format) > 48
                or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9+._-]*(?:/[A-Za-z0-9][A-Za-z0-9+._-]*)?",
                    safe_format,
                )):
            safe_format = None
        # Legacy catalogue rows may predate the validated Core mirror or
        # originate in an external importer. Retain only typed bibliographic
        # text; never re-export nested metadata, embedded signed links, paths,
        # credentials or error strings as a stale public catalogue record.
        public_meta: dict[str, Any] = {}
        for name in _PUBLIC_META | frozenset(_PUBLIC_SEARCH_TEXT_FIELDS):
            if name not in meta:
                continue
            verified = _safe_public_search_text(name, meta[name])
            if verified is not None:
                public_meta[name] = verified
        candidates.append({
            "key": catalog_id, "item_id": item_id,
            "title": safe_title, "author": safe_author,
            "provider_id": provider_id, "identity_id": identity_id,
            "media_format": safe_format,
            "medium": str(public_meta.get("medium") or "unknown"),
            "metadata": public_meta, "mirrored_at": mirrored,
        })
    matched = filter_loaded_results(
        candidates, query, providers=(provider_id,), scope=scope,
        field=field, content_types=content_types,
    )
    matched_count = len(matched)
    if not matched_count:
        return empty("no_known_results")
    first = page * page_size
    if first >= matched_count:
        return KnownPublicFallbackPage((), "page_out_of_range", page, page_size, matched_count, False)
    rows: list[SearchResult] = []
    for row in matched[first:first + page_size]:
        entry_meta = row["metadata"]
        rows.append(SearchResult(
            id="known-stale:" + row["key"], record_id=row["item_id"],
            docbase=provider_id, medium=row["medium"], title=str(row.get("title") or "Sans titre"),
            author=row.get("author"), type=None, format=row.get("media_format"),
            publisher=entry_meta.get("publisher"), published_at=entry_meta.get("published_at"),
            description=entry_meta.get("description"),
            thumbnail_url=None, friendly_url=None,
            scenario="KNOWN_CATALOG_STALE", source_provider=provider_id,
            source_credential="known-public", source_collection="catalog",
            owned=False, downloadable=False, metadata={
                **entry_meta, "search_provenance": "known_catalog_stale",
                "mirrored_at": row["mirrored_at"],
            },
        ))
    return KnownPublicFallbackPage(
        tuple(rows), "known_catalog_stale", page, page_size,
        matched_count, first + len(rows) < matched_count,
    )


def known_public_fallback(
    storage: Any, *, provider_id: str, identity_id: str | None,
    query: str, scope: str, field: str, content_types: tuple[str, ...],
    now: int | None = None,
) -> tuple[SearchResult, ...]:
    """Legacy first page API, unchanged for synchronous/progressive clients."""
    return known_public_fallback_page(
        storage, provider_id=provider_id, identity_id=identity_id,
        query=query, scope=scope, field=field, content_types=content_types,
        now=now, page=0, page_size=_MAX_RESULTS,
    ).results
