"""Source-exact Search detail-to-list cover bridge (P1 #6168, N4 tranche 1).

A same-notice holdings result may arrive after the Search list row is
materialised. The Core N2 decision, not a Gtk cell or provider DTO, remembers
its public cover for another GTK row projection in the *same* search epoch.
The existing SearchDetail callback still owns the live selection/generation
guard before calling publish(). No rights, provider I/O or metadata rewriting.

This cache is window-local. It does NOT join another notice on title, ISBN,
source record ID alone, or a shared image URL. Consumers must provide the
same captured identity and all four UI/Core context generations.
"""
from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
import json

from ..core.book_cover_cache import CoverDecisionCache
from ..core.book_cover_projection import CoverDocumentScope
from ..core.covers import candidate
from ..models import ItemDetail, SearchResult


def _exact(value: object, *, allow_empty: bool = False) -> bool:
    return (
        type(value) is str
        and len(value) <= 512
        and (allow_empty or bool(value))
        and value.isprintable()
    )


def _scope(item: SearchResult, identity_id: str) -> CoverDocumentScope | None:
    if (type(item) is not SearchResult or not _exact(identity_id)
            or not _exact(item.source_provider)
            or not _exact(item.record_id)
            or not _exact(item.docbase)
            or not _exact(item.source_credential, allow_empty=True)):
        return None
    extras = (
        getattr(item, "source_collection", None),
        getattr(item, "source_account_id", None),
        getattr(item, "revision_id", None),
    )
    if any(value is not None and not _exact(value, allow_empty=True)
           for value in extras):
        return None
    coordinates = (
        item.source_provider, item.source_credential, item.record_id,
        item.docbase, *(value if value is not None else None for value in extras),
    )
    digest = sha256(
        json.dumps(coordinates, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return CoverDocumentScope(
        kind="catalogue", identity_id=identity_id,
        source_key="search-exact:" + digest,
        provider_id=item.source_provider,
        credential_id=item.source_credential,
        account_id=item.source_account_id,
    )


def _context(values: object) -> tuple[int, int, int, int] | None:
    if (type(values) is not tuple or len(values) != 4
            or any(type(value) is not int or value < 0 for value in values)):
        return None
    return values


class SearchCoverBridge:
    """At most 128 exact scoped decisions; reusable across GTK row rebuilds."""

    def __init__(self, *, max_sources: int = 128):
        if type(max_sources) is not int or not 1 <= max_sources <= 1024:
            raise ValueError("invalid search cover scope bound")
        self._cache = CoverDecisionCache(max_scopes=max_sources)
        self._records: OrderedDict[
            CoverDocumentScope, tuple[tuple[int, int, int, int], int],
        ] = OrderedDict()
        self._revision = 0
        self._limit = max_sources

    def publish(
        self, item: SearchResult, detail: ItemDetail, *,
        identity_id: str, context: tuple[int, int, int, int],
    ) -> bool:
        """Only a selected exact holdings/notice produces a new N2 decision."""
        scope = _scope(item, identity_id)
        generation = _context(context)
        if (scope is None or generation is None or type(detail) is not ItemDetail
                or detail.source_provider != item.source_provider
                or detail.source_credential != item.source_credential
                or detail.record_id != item.record_id
                or detail.docbase != item.docbase):
            return False
        cover = candidate(
            detail.thumbnail_url, tier="origin",
            source="search-exact-holdings",
        )
        if cover is None:
            return False
        # An exact holdings cover is stronger than the source list's
        # catalogue thumbnail; Core still checks private URL admissibility.
        self._revision += 1
        revision = self._revision
        token = self._cache.begin(scope, revision=revision)
        published = self._cache.resolve_and_publish(
            token, (cover,), snapshot_complete=True,
        )
        if not published:
            return False
        self._records[scope] = (generation, revision)
        self._records.move_to_end(scope)
        while len(self._records) > self._limit:
            self._records.popitem(last=False)
        return True

    def selected_url(
        self, item: SearchResult, *,
        identity_id: str, context: tuple[int, int, int, int],
    ) -> str | None:
        """No reuse after query, identity, account or source epoch changes."""
        scope = _scope(item, identity_id)
        generation = _context(context)
        if scope is None or generation is None:
            return None
        known = self._records.get(scope)
        if known is None or known[0] != generation:
            return None
        decision = self._cache.peek(scope, revision=known[1])
        if decision is None or decision.status != "selected":
            return None
        self._records.move_to_end(scope)
        return decision.uri

    def invalidate(
        self, item: SearchResult, *, identity_id: str,
    ) -> None:
        scope = _scope(item, identity_id)
        if scope is not None:
            self._records.pop(scope, None)
            self._cache.invalidate(scope, cause="evidence_changed")

    def stats(self) -> dict[str, int]:
        return {"scopes": len(self._records)}


__all__ = ["SearchCoverBridge"]
