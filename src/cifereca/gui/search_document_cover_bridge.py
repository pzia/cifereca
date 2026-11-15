"""One exact remote Search occurrence -> shared N2 cover snapshot (#6168).

Provider SearchResult owns only its OWN catalogue thumbnail. The stronger
origin image can come only from a same-notice, same-four-epoch SearchCoverBridge
holdings decision. No provider I/O, rights inference, title/ISBN/URL join,
EPUB scan, or N3 physical-cache check occurs on the GTK path.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from ..core.book_cover_projection import CoverDocumentScope
from ..core.notice_cover_repository import is_notice_cover_document
from ..core.search_cover_provenance import project_exact_search_catalogue_cover
from ..core.covers import CoverCandidate, candidate
from ..models import SearchResult
from .document_cover_service import CoverSourceSnapshot, DocumentCoverService
from .search_cover_bridge import SearchCoverBridge, _scope


@dataclass(frozen=True, slots=True)
class VerifiedSearchDocumentCover:
    """One still-live SearchResult object and its exact documentary sources."""

    source_item: SearchResult = field(compare=False)
    row_key: str
    document: CoverDocumentScope
    context: tuple[int, int, int, int]
    candidates: tuple[CoverCandidate, ...]

    @property
    def row_signature(self) -> tuple[int, VerifiedSearchDocumentCover]:
        return id(self.source_item), self


def search_cover_context(window: Any) -> tuple[int, int, int, int] | None:
    service = getattr(window, "service", None)
    context = (
        getattr(window, "_identity_context_generation", None),
        getattr(window, "_search_generation", None),
        getattr(service, "_account_context_epoch", None),
        getattr(service, "_source_context_epoch", None),
    )
    if any(type(epoch) is not int or epoch < 0 for epoch in context):
        return None
    return context


def _current(
    window: Any, item: SearchResult, row_key: str,
) -> tuple[CoverDocumentScope, tuple[int, int, int, int]] | None:
    identity_id = getattr(
        getattr(getattr(window, "service", None), "current_identity", None),
        "identity_id", None,
    )
    objects = getattr(window, "search_objects", None)
    context = search_cover_context(window)
    if (getattr(window, "_closed", False)
            or getattr(window, "_search_mode", None) != "global"
            or type(item) is not SearchResult
            or type(row_key) is not str or not row_key
            or type(objects) is not dict or objects.get(row_key) is not item
            or context is None
            or getattr(window, "_online_cover_context", None) != context
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
    metadata = item.metadata
    if (type(metadata) is not dict
            or metadata.get("search_provenance") == "known_catalog_stale"):
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
    catalogue_facts = project_exact_search_catalogue_cover(
        item, identity_id=scope.identity_id, share_public=True,
    )
    # Core, never GTK or the N3 image cache, is the source of the direct
    # catalogue evidence and owns the exact source-identity coordinates.
    if catalogue_facts is not None:
        catalogue = catalogue_facts.catalogue
        if (catalogue_facts.scope.kind == "public_catalogue"
                and (not candidates or candidates[0].url == catalogue.url)):
            # Some anonymous holdings render the listing's own image again.
            # An identical public URL adds no independently scoped source.
            # A distinct holdings image stays under the active profile.
            scope = catalogue_facts.scope
            candidates.clear()
        candidates.append(catalogue)
    if not candidates:
        return None
    # Only a real Core producer may attach an already-persisted Notice C
    # reference to its very SearchResult object. Prefer the canonical source
    # when the sole evidence is that public catalogue field. A different
    # holdings/origin image is stronger and stays on the exact legacy path
    # until its own qualified Notice C origin attribute exists.
    admitted = getattr(item, "_core_notice_cover_document_ref", None)
    if (scope.kind == "public_catalogue"
            and len(candidates) == 1
            and catalogue_facts is not None
            and candidates[0] == catalogue_facts.catalogue
            and type(admitted) is CoverDocumentScope
            and is_notice_cover_document(admitted)
            and admitted.kind == "public_catalogue"
            and admitted.provider_id == item.source_provider
            and admitted.identity_id is None):
        scope = admitted
    return VerifiedSearchDocumentCover(
        source_item=item, row_key=row_key,
        document=scope, context=context,
        candidates=tuple(candidates),
    )


@dataclass(frozen=True, slots=True)
class _RegisteredSearchCover:
    candidates: tuple[CoverCandidate, ...]
    revision: int
    private_evidence: VerifiedSearchDocumentCover | None


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
            CoverDocumentScope, _RegisteredSearchCover,
        ] = OrderedDict()

    def register(
        self, service: DocumentCoverService, evidence: VerifiedSearchDocumentCover,
    ) -> CoverDocumentScope:
        """Publish one exact, source-owned N1 choice before N3 read."""
        if (type(service) is not DocumentCoverService
                or type(evidence) is not VerifiedSearchDocumentCover):
            raise TypeError("expected exact remote Search evidence and service")
        if is_notice_cover_document(evidence.document):
            # Notice C is read through by the shared worker. Never inject
            # another unversioned Search source under its exact N2 ref.
            raise ValueError("notice_c_source_requires_readthrough")
        with self._lock:
            if self._closed:
                raise RuntimeError("Search document registry closed")
            known = self._known.get(evidence.document)
            is_public = evidence.document.kind == "public_catalogue"
            same = known is not None and known.candidates == evidence.candidates
            if same and not is_public:
                prior = known.private_evidence
                same = (prior is not None and prior.source_item is evidence.source_item
                        and prior == evidence)
            revision = known.revision if same else self._issued + 1
            snapshot = CoverSourceSnapshot(
                scope=evidence.document, revision=revision,
                candidates=evidence.candidates, snapshot_complete=True,
            )
            # Refuse stale/replaced evidence before advancing the registry.
            service.register(snapshot)
            if not same:
                self._issued = revision
            # Shared entries carry the source candidates, not the active
            # row object, identity or navigation epochs of their consumer.
            self._known[evidence.document] = _RegisteredSearchCover(
                evidence.candidates, revision, None if is_public else evidence,
            )
            self._known.move_to_end(evidence.document)
            while len(self._known) > self._limit:
                self._known.popitem(last=False)
            return evidence.document

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._known.clear()


def search_document_is_current(window: Any, evidence: VerifiedSearchDocumentCover) -> bool:
    current = exact_remote_search_document(
        window, evidence.source_item, evidence.row_key,
    )
    if evidence == current:
        return True
    # Progressive results may already have been bound under the legacy
    # exact public scope when their source is persisted at batch completion.
    # Keep that SAME source/candidates/context callback valid until a GTK
    # rebind. Never convert an old callback into a NEW cover association.
    return (
        type(current) is VerifiedSearchDocumentCover
        and type(evidence) is VerifiedSearchDocumentCover
        and current.source_item is evidence.source_item
        and current.row_key == evidence.row_key
        and current.context == evidence.context
        and current.candidates == evidence.candidates
        and evidence.document.kind == "public_catalogue"
        and evidence.document.source_key.startswith("search-exact:")
        and is_notice_cover_document(current.document)
    )


def selected_search_document(window: Any, item: SearchResult) -> VerifiedSearchDocumentCover | None:
    page = getattr(window, "search_page", None)
    selected = getattr(getattr(page, "selection", None), "get_selected_item", None)
    row = selected() if callable(selected) else None
    return exact_remote_search_document(window, item, getattr(row, "key", None))


def attach_search_document(row: Any, evidence: VerifiedSearchDocumentCover) -> None:
    row._cover_search_evidence = evidence
    row._cover_document_ref = evidence.document


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
    if not search_document_is_current(window, evidence):
        return False
    if is_notice_cover_document(document):
        # A producer-stamped exact source belongs to persisted Notice C.
        # Do not also publish a competing N2 source: the widget calls the
        # common cache, whose bounded worker reads the source/epoch from C.
        return (
            getattr(item, "_core_notice_cover_document_ref", None) == document
            and callable(getattr(service, "_cached_source_reader", None))
        )
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
