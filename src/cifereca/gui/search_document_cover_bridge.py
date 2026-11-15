"""One exact remote Search occurrence -> shared N2 cover snapshot (#6168).

Provider SearchResult owns only its OWN catalogue thumbnail. The stronger
origin image can come only from a same-notice, same-four-epoch SearchCoverBridge
holdings decision. No provider I/O, rights inference, title/ISBN/URL join,
EPUB scan, or N3 physical-cache check occurs on the GTK path.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

from ..core.book_cover_projection import CoverDocumentScope
from ..core.covers import CoverCandidate, candidate
from ..models import SearchResult
from .document_cover_service import CoverSourceSnapshot, DocumentCoverService
from .search_cover_bridge import SearchCoverBridge, _scope


@dataclass(frozen=True, slots=True)
class VerifiedSearchDocumentCover:
    """One still-live SearchResult object and its exact documentary sources."""

    source_item: SearchResult
    row_key: str
    document: CoverDocumentScope
    context: tuple[int, int, int, int]
    candidates: tuple[CoverCandidate, ...]


def _current(
    window: Any, item: SearchResult, row_key: str,
) -> tuple[CoverDocumentScope, tuple[int, int, int, int]] | None:
    service = getattr(window, "service", None)
    identity_id = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    objects = getattr(window, "search_objects", None)
    context = (
        getattr(window, "_identity_context_generation", None),
        getattr(window, "_search_generation", None),
        getattr(service, "_account_context_epoch", None),
        getattr(service, "_source_context_epoch", None),
    )
    if (getattr(window, "_closed", False)
            or getattr(window, "_search_mode", None) != "global"
            or type(item) is not SearchResult
            or type(row_key) is not str or not row_key
            or type(objects) is not dict or objects.get(row_key) is not item
            or any(type(epoch) is not int or epoch < 0 for epoch in context)
            or type(identity_id) is not str or not identity_id):
        return None
    # A rendered result from an old query must not acquire a *fresh*
    # documentary source epoch just because the user changed a filter.
    # The original online batch was explicitly bound to this query state.
    controls = getattr(getattr(window, "search_controls", None), "state", None)
    if not callable(controls):
        return None
    try:
        state = controls()
        expected_query = (state.text, state.field, state.scope, identity_id)
    except (AttributeError, TypeError, ValueError):
        return None
    if getattr(window, "_loaded_search_context", None) != expected_query:
        return None
    scope = _scope(item, identity_id)
    return (scope, context) if scope is not None else None


def exact_remote_search_document(
    window: Any, item: SearchResult, row_key: str,
) -> VerifiedSearchDocumentCover | None:
    """Prepare a packet, without registering or materializing every result.

    Registration is deferred until a GTK cell or detail is actually bound.
    An old provider result, even if it shares a URL/title/ISBN with another
    result, cannot borrow the latter's source association.
    """
    admitted = _current(window, item, row_key)
    if admitted is None:
        return None
    scope, context = admitted
    metadata = item.metadata
    if (type(metadata) is not dict
            or metadata.get("search_provenance") == "known_catalog_stale"):
        return None
    candidates: list[CoverCandidate] = []
    bridge = getattr(window, "_search_cover_bridge", None)
    if type(bridge) is SearchCoverBridge:
        origin_uri = bridge.selected_url(
            item, identity_id=scope.identity_id, context=context,
        )
        origin = candidate(
            origin_uri, tier="origin", source="search-exact-holdings",
            evidence_id=scope.source_key,
        )
        if origin is not None:
            candidates.append(origin)
    catalogue = candidate(
        item.thumbnail_url, tier="catalog", source="search-result",
        evidence_id=scope.source_key,
    )
    if catalogue is not None:
        candidates.append(catalogue)
    if not candidates:
        return None
    return VerifiedSearchDocumentCover(
        source_item=item, row_key=row_key,
        document=scope, context=context,
        candidates=tuple(candidates),
    )


class SearchDocumentCoverRegistry:
    """Window-owned monotone revisions; physically shared N3 remains external."""

    def __init__(self, *, max_sources: int = 1024):
        if type(max_sources) is not int or not 1 <= max_sources <= 4096:
            raise ValueError("invalid search cover registry bound")
        self._lock = Lock()
        self._limit = max_sources
        self._issued = 0
        self._closed = False
        self._known: OrderedDict[
            CoverDocumentScope, tuple[VerifiedSearchDocumentCover, int],
        ] = OrderedDict()

    def register(
        self, service: DocumentCoverService, evidence: VerifiedSearchDocumentCover,
    ) -> CoverDocumentScope:
        """Publish one exact, source-owned N1 choice before N3 read."""
        if (type(service) is not DocumentCoverService
                or type(evidence) is not VerifiedSearchDocumentCover):
            raise TypeError("expected exact remote Search evidence and service")
        with self._lock:
            if self._closed:
                raise RuntimeError("Search document registry closed")
            known = self._known.get(evidence.document)
            if (known is not None
                    and known[0].source_item is evidence.source_item
                    and known[0].row_key == evidence.row_key
                    and known[0].document == evidence.document
                    and known[0].context == evidence.context
                    and known[0].candidates == evidence.candidates):
                revision = known[1]
            else:
                revision = self._issued + 1
            snapshot = CoverSourceSnapshot(
                scope=evidence.document, revision=revision,
                candidates=evidence.candidates, snapshot_complete=True,
            )
            # Refuse stale/replaced evidence before advancing the registry.
            service.register(snapshot)
            if known is None or revision != known[1]:
                self._issued = revision
            self._known[evidence.document] = (evidence, revision)
            self._known.move_to_end(evidence.document)
            while len(self._known) > self._limit:
                self._known.popitem(last=False)
            return evidence.document

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._known.clear()


def register_exact_search_document(
    window: Any, item: SearchResult,
    service: DocumentCoverService,
    evidence: VerifiedSearchDocumentCover,
    document: CoverDocumentScope,
) -> bool:
    """Re-admit live provider object and all four epochs at GTK bind time."""
    if (type(service) is not DocumentCoverService
            or type(evidence) is not VerifiedSearchDocumentCover
            or type(document) is not CoverDocumentScope
            or evidence.document != document
            or evidence.source_item is not item):
        return False
    current = exact_remote_search_document(
        window, item, evidence.row_key,
    )
    if (current is None or current.source_item is not item
            or current.row_key != evidence.row_key
            or current.document != evidence.document
            or current.context != evidence.context
            or current.candidates != evidence.candidates):
        return False
    adapter = getattr(window, "search_document_cover_registry", None)
    if type(adapter) is not SearchDocumentCoverRegistry:
        return False
    try:
        return adapter.register(service, evidence) == document
    except (TypeError, ValueError, RuntimeError):
        return False


__all__ = [
    "VerifiedSearchDocumentCover", "SearchDocumentCoverRegistry",
    "exact_remote_search_document", "register_exact_search_document",
]
