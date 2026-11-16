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
    # Search's visible keys can include an exact source/credential/edition
    # suffix when two supplier rows reuse the same legacy display ID. Locate
    # the ONE actual live SearchResult object, never join an edition by its
    # title, URL, ISBN, display ID, or even identical source coordinates.
    objects = getattr(window, "search_objects", None)
    if type(objects) is not dict:
        return None
    key = None
    for candidate_key, row in objects.items():
        if row is not item:
            continue
        if key is not None or type(candidate_key) is not str or not candidate_key:
            return None  # Ambiguous same object mapped under two list keys.
        key = candidate_key
    if key is None or _current(window, item, key) is None:
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
