"""Reuse the exact loaded Search cover in a Core-rendered recommendation detail."""
from __future__ import annotations

from ..models import ItemDetail, SearchResult
from .search_document_cover_bridge import (
    VerifiedSearchDocumentCover,
    _current,
    exact_remote_search_document,
)


def loaded_recommendation_cover(
    window, item: SearchResult, detail: ItemDetail | None = None,
) -> VerifiedSearchDocumentCover | None:
    """The exact detail port supplies a live Core row, never a plugin image."""
    if type(item) is not SearchResult:
        return None
    key = f"{item.source_provider}:{item.id}"
    if _current(window, item, key) is None:
        return None
    if detail is not None:
        store = window.search_page.store
        # That exact Search row can be unselected while Recommendations is
        # visible. Publish fresh holdings to it without selecting it or
        # asking Search to make a second request.
        for index in range(store.get_n_items()):
            row = store.get_item(index)
            if row.key == key:
                from .search_details import _publish_search_detail_cover

                _publish_search_detail_cover(window, item, detail, row=row)
                break
    return exact_remote_search_document(window, item, key)
