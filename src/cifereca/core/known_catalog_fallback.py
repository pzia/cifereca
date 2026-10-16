"""Conservative offline fallback for a failed *public* provider search (#1916).

Only mirrored public catalogue rows of the active identity may be returned.
This is not a purchase, loan, holding, download or fresh provider result.
"""
from __future__ import annotations

import time
from typing import Any
from xml.etree.ElementTree import ParseError

import httpx

from ..core.search import filter_loaded_results
from ..models import SearchResult

_MAX_CATALOG_ROWS = 10000
_MAX_RESULTS = 50
_MAX_AGE_SECONDS = 90 * 86400
_PUBLIC_META = frozenset({
    "subjects", "subject", "series", "collection", "publisher", "languages",
    "language", "isbn", "isbns", "authors", "contributors", "genre",
    "genres", "categories", "medium", "description", "published_at",
})


def public_service_unavailable(exc: BaseException) -> bool:
    """A remote transport/protocol outage, never an auth or rights refusal."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if getattr(exc, "code", None) == "search_temporarily_unavailable":
            return True
        if isinstance(exc, (httpx.TransportError, ParseError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            return code == 429 or code >= 500
        exc = exc.__cause__ or exc.__context__
    return False


def known_public_fallback(
    storage: Any, *, provider_id: str, identity_id: str | None,
    query: str, scope: str, field: str, content_types: tuple[str, ...],
    now: int | None = None,
) -> tuple[SearchResult, ...]:
    """Read bounded, fresh-enough catalogue evidence; no service or network IO."""
    if not identity_id or len(query.strip()) < 2 or provider_id in {"", "numilog"}:
        return ()
    current = int(time.time()) if now is None else int(now)
    try:
        entries = storage.catalog_entries()
        if len(entries) > _MAX_CATALOG_ROWS:
            return ()
    except Exception:
        return ()
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
        # Authenticated libraries and signed source links never become public.
        if str(meta.get("source_collection") or "").casefold() in {
            "library", "personal", "owned", "loans",
        }:
            continue
        mirrored = meta.get("mirrored_at")
        if (type(mirrored) is not int or mirrored > current
                or current - mirrored > _MAX_AGE_SECONDS):
            continue
        item_id = entry.get("item_id")
        catalog_id = entry.get("catalog_entry_id")
        if not all(type(v) is str and v and len(v) < 512 for v in (item_id, catalog_id)):
            continue
        public_meta = {key: value for key, value in meta.items() if key in _PUBLIC_META}
        candidates.append({
            "key": catalog_id, "item_id": item_id,
            "title": entry.get("title"), "author": entry.get("author"),
            "provider_id": provider_id, "identity_id": identity_id,
            "media_format": entry.get("media_format"),
            "medium": str(public_meta.get("medium") or "unknown"),
            "metadata": public_meta, "mirrored_at": mirrored,
        })
    matched = filter_loaded_results(
        candidates, query, providers=(provider_id,), scope=scope,
        field=field, content_types=content_types,
    )
    rows: list[SearchResult] = []
    for row in matched[:_MAX_RESULTS]:
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
    return tuple(rows)
