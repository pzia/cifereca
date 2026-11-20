"""Reuse a *selected exact Core N2* Search cover for detail presentation.

The producer-specific SourceKey and all four live context epochs must match
the currently selected GTK row. This is an opt-in, read-only visual fallback:
neither a catalogue title/ISBN nor a stored image URL is a documentary join.

A previously attested holdings image can remain available when the next
provider detail omits its image or a list cell is recycled. The stored Core
decision expires/invalidate according to N2; no rights, Loan, SearchResult or
ItemDetail DTO is changed and no provider requests are made.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..models import ItemDetail, SearchResult
from .search_cover_bridge import SearchCoverBridge


def cached_exact_selected_search_cover(window: Any, item: SearchResult) -> str | None:
    """Only reuse a live *same-notice* N2 decision for a selected Search row."""
    if type(item) is not SearchResult:
        return None
    service = getattr(window, "service", None)
    identity_id = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    bridge = getattr(window, "_search_cover_bridge", None)
    if (type(identity_id) is not str or not identity_id
            or type(bridge) is not SearchCoverBridge):
        return None

    page = getattr(window, "search_page", None)
    selection = getattr(page, "selection", None)
    row = selection.get_selected_item() if selection is not None else None
    key = getattr(row, "key", None)
    if type(key) is not str or not key:
        return None
    mode = getattr(window, "_search_mode", None)
    if mode == "global":
        sources = getattr(window, "search_objects", None)
        if type(sources) is not dict or sources.get(key) is not item:
            return None
    elif mode == "local":
        sources = getattr(window, "_search_local_objects", None)
        if type(sources) is not dict:
            return None
        raw = sources.get(key)
        if type(raw) is not dict:
            return None
        # The known-catalogue v2 gate binds the returned item to the actual
        # local row's identity/provider/credential/edition/collection/account.
        from ..core.known_catalog_provenance import known_catalog_search_result
        qualified = known_catalog_search_result(raw, identity_id=identity_id)
        if (qualified is None or (
            qualified.id, qualified.source_provider, qualified.source_credential,
            qualified.record_id, qualified.docbase, qualified.medium,
            qualified.source_collection, qualified.source_account_id,
            qualified.revision_id,
        ) != (
            item.id, item.source_provider, item.source_credential,
            item.record_id, item.docbase, item.medium,
            item.source_collection, item.source_account_id,
            item.revision_id,
        )):
            return None
    else:
        return None

    return bridge.selected_url(
        item, identity_id=identity_id,
        context=(
            getattr(window, "_identity_context_generation", None),
            getattr(window, "_search_generation", None),
            getattr(service, "_account_context_epoch", None),
            getattr(service, "_source_context_epoch", None),
        ),
    )


def visual_search_item(window: Any, item: SearchResult) -> SearchResult:
    """Presentation-only copy for the initial detail while holdings loads."""
    cover = cached_exact_selected_search_cover(window, item)
    if cover is None or item.thumbnail_url:
        return item
    return replace(item, thumbnail_url=cover)


def visual_search_detail(
    window: Any, item: SearchResult, detail: ItemDetail,
) -> ItemDetail:
    """Do not overwrite newer actual holdings evidence or the supplier DTO."""
    if type(item) is not SearchResult or type(detail) is not ItemDetail:
        return detail
    if (detail.thumbnail_url
            or detail.source_provider != item.source_provider
            or detail.source_credential != item.source_credential
            or detail.record_id != item.record_id
            or detail.docbase != item.docbase):
        return detail
    cover = cached_exact_selected_search_cover(window, item)
    if cover is None:
        return detail
    return replace(detail, thumbnail_url=cover)


__all__ = [
    "cached_exact_selected_search_cover",
    "visual_search_item", "visual_search_detail",
]
