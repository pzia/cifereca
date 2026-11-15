"""Exact History thumbnail projection shared by list and detail (P1 #6168).

HistoryItem.thumbnail_url is direct provider image evidence for *one recorded
history occurrence*, not proof that a homonymous catalogue/loan owns it.
Never promote results from the legacy title-based history_cover_resolver here.
No provider I/O, file scan, rights changes, GTK objects or DTO mutation.

The window owns a private bounded N2 cache; it supplies the active account
epoch and an explicit History producer revision. Old cached images cannot
survive A→B→A or a newer History read with changed thumbnails. Detail and
list share the same N1 immutable decision under the exact source coordinates.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from hashlib import sha256
import json
from typing import Any

from ..core.book_cover_cache import CoverDecisionCache
from ..core.book_cover_projection import (
    CoverDocumentScope, ResolvedBookCover, resolve_book_cover,
)
from ..core.covers import candidate
from ..models import HistoryItem


def _coordinate(value: object, *, allow_empty: bool = False) -> bool:
    return (
        type(value) is str and len(value) <= 512
        and value.isprintable() and (allow_empty or bool(value))
    )


def exact_history_cover_scope(
    item: HistoryItem, *, identity_id: str,
) -> CoverDocumentScope | None:
    """One source-attested History occurrence, never an ISBN/title join."""
    if (type(item) is not HistoryItem or not _coordinate(identity_id)
            or item.identity_id != identity_id
            or not _coordinate(item.id)
            or not _coordinate(item.record_id)
            or not _coordinate(item.source_provider)
            or not _coordinate(item.source_credential)
            or not _coordinate(item.source_account_id, allow_empty=True)
            or not _coordinate(item.medium)):
        return None
    coordinates = (
        identity_id, item.source_provider, item.source_credential,
        item.source_account_id, item.id, item.record_id, item.medium,
    )
    digest = sha256(
        json.dumps(
            coordinates, ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return CoverDocumentScope(
        kind="catalogue",
        identity_id=identity_id,
        source_key="history-exact:" + digest,
        provider_id=item.source_provider,
        credential_id=item.source_credential,
        account_id=item.source_account_id,
    )


class HistoryCoverDecisionBridge:
    """Bounded memoization of N1 decisions already admitted in N2."""

    def __init__(self, *, max_occurrences: int = 512):
        if type(max_occurrences) is not int or not 1 <= max_occurrences <= 4096:
            raise ValueError("invalid History cover capacity")
        self._cache = CoverDecisionCache(max_scopes=max_occurrences)
        self._records: OrderedDict[
            CoverDocumentScope, tuple[tuple[str, int, int], str | None, int],
        ] = OrderedDict()
        self._revision = 0
        self._capacity = max_occurrences

    def resolve(
        self, item: HistoryItem, *, identity_id: str,
        account_epoch: int, history_revision: int,
    ) -> ResolvedBookCover | None:
        scope = exact_history_cover_scope(item, identity_id=identity_id)
        if (scope is None or type(account_epoch) is not int
                or account_epoch < 0 or type(history_revision) is not int
                or history_revision < 1):
            return None
        # The original History DTO is the only cover evidence; the generic
        # default image is visual fallback, not a documentary cover proof.
        url = item.thumbnail_url
        if url is not None and type(url) is not str:
            return None
        context = (identity_id, account_epoch, history_revision)
        known = self._records.get(scope)
        if known is not None and known[0] == context and known[1] == url:
            cached = self._cache.peek(scope, revision=known[2])
            if cached is not None:
                self._records.move_to_end(scope)
                return cached
        self._revision += 1
        revision = self._revision
        direct = candidate(
            url, tier="catalog", source=item.source_provider,
            evidence_id=item.id,
        )
        decision = resolve_book_cover(
            scope, (direct,) if direct is not None else (),
            revision=revision, snapshot_complete=True,
        )
        token = self._cache.begin(scope, revision=revision)
        if not self._cache.publish_resolved(token, decision):
            return None
        self._records[scope] = (context, url, revision)
        self._records.move_to_end(scope)
        while len(self._records) > self._capacity:
            self._records.popitem(last=False)
        return decision

    def stats(self) -> dict[str, int]:
        return {"occurrences": len(self._records)}


def history_cover_presentation(
    window: Any, item: HistoryItem,
) -> HistoryItem:
    """Return an image-only copy for row/detail; keep actionable DTO intact."""
    if type(item) is not HistoryItem:
        return item
    service = getattr(window, "service", None)
    identity_id = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    account_epoch = getattr(service, "_account_context_epoch", None)
    producer_revision = getattr(window, "_history_cover_revision", None)
    if (type(identity_id) is not str or not identity_id
            or type(account_epoch) is not int or account_epoch < 0
            or type(producer_revision) is not int or producer_revision < 1):
        return item
    bridge = getattr(window, "_history_cover_decisions", None)
    if bridge is None:
        bridge = HistoryCoverDecisionBridge()
        window._history_cover_decisions = bridge
    if type(bridge) is not HistoryCoverDecisionBridge:
        return item
    decision = bridge.resolve(
        item, identity_id=identity_id,
        account_epoch=account_epoch, history_revision=producer_revision,
    )
    if decision is None:
        return item  # unknown owner/source: never invent documentary linkage
    selected = decision.uri if decision.status == "selected" else None
    return replace(item, thumbnail_url=selected) if selected != item.thumbnail_url else item


__all__ = [
    "HistoryCoverDecisionBridge", "exact_history_cover_scope",
    "history_cover_presentation",
]
