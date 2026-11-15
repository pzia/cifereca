"""Exact loan-owned supplier cover -> shared documentary N2 (#6168).

The Loan DTO is itself the source of this catalogue-tier thumbnail. It may
not borrow a URL, placement or permission from a homonymous Library record.
A stronger, independently attested local source remains owned by the existing
EPUB/origin bridge; this adapter never downgrades it because N3 is cold.

GTK only binds an exact DocumentRef. No provider request, EPUB inspection,
file/cache hit, network I/O or rights-changing operation is performed here.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from ..core.book_cover_projection import CoverDocumentScope
from ..core.book_cover_source_projection import exact_loan_cover_scope
from ..core.covers import CoverCandidate, candidate
from ..core.loan_source_identity import loan_source_key
from ..models import Loan
from .document_cover_service import CoverSourceSnapshot, DocumentCoverService


@dataclass(frozen=True, slots=True)
class VerifiedLoanSupplierCover:
    """Original active loan, exact source, and four independently moving epochs."""

    source_item: Loan
    document: CoverDocumentScope
    candidate: CoverCandidate
    context: tuple[int, int, int, int]


def _current(host: Any, loan: Loan):
    service = getattr(host, "service", None)
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    context = (
        getattr(host, "_identity_context_generation", None),
        getattr(service, "_account_context_epoch", None),
        getattr(service, "_source_context_epoch", None),
        getattr(host, "_loan_cover_decision_revision", None),
    )
    if (getattr(host, "_closed", False)
            or type(loan) is not Loan or type(identity) is not str or not identity
            or loan.identity_id != identity
            or any(type(n) is not int or n < 0 for n in context)
            or context[-1] < 1
            or getattr(host, "loan_objects", {}).get(loan_source_key(loan)) is not loan):
        return None
    scope = exact_loan_cover_scope(loan)
    return (scope, context) if scope is not None else None


def exact_supplier_loan_cover(
    host: Any, loan: Loan, *, displayed: Loan,
) -> VerifiedLoanSupplierCover | None:
    """Admit an original, currently displayed supplier image, never a fallback.

    When a complete current Library projection exists, defer to its Core N1
    decision. An attested higher-tier origin/EPUB or an ambiguous decision
    cannot be silently replaced by a supplier image merely because it is
    already cached in N3. When no Library proof exists, the loan's own image
    remains independently admissible at catalogue tier.
    """
    live = _current(host, loan)
    if (live is None or type(displayed) is not Loan
            or displayed.thumbnail_url != loan.thumbnail_url):
        return None
    scope, context = live
    supplier = candidate(
        loan.thumbnail_url, tier="catalog", source=scope.provider_id,
        evidence_id=scope.loan_id, evidence_strength=0,
    )
    if supplier is None:
        return None
    projection = getattr(host, "_loan_cover_projection", None)
    if (type(projection) is tuple and len(projection) == 3
            and projection[0] == context[1] and projection[1] == scope.identity_id
            and type(projection[2]) is tuple and projection[2]):
        from .loan_cover_decision_bridge import loan_cover_decision
        decision = loan_cover_decision(host, loan, projection[2])
        if (decision is None or decision.status != "selected"
                or decision.scope != scope or decision.tier != "catalog"
                or decision.uri != supplier.url):
            return None
    return VerifiedLoanSupplierCover(loan, scope, supplier, context)


class LoanSupplierCoverRegistry:
    """Bounded per-host revision allocator, independent of shared N3 bytes."""

    def __init__(self, max_sources: int = 1024):
        if type(max_sources) is not int or not 1 <= max_sources <= 4096:
            raise ValueError("invalid loan source capacity")
        self._lock = Lock()
        self._limit = max_sources
        self._issued = 0
        self._known: OrderedDict[
            CoverDocumentScope, tuple[VerifiedLoanSupplierCover, int],
        ] = OrderedDict()

    def register(
        self, service: DocumentCoverService,
        evidence: VerifiedLoanSupplierCover,
    ) -> CoverDocumentScope:
        if (type(service) is not DocumentCoverService
                or type(evidence) is not VerifiedLoanSupplierCover):
            raise TypeError("expected attested loan and shared cover service")
        with self._lock:
            prior = self._known.get(evidence.document)
            # Loan is a mutable domain DTO. Re-admit its immutable URL and
            # four epochs at the call site before trusting equality here.
            same = (
                prior is not None
                and prior[0].source_item is evidence.source_item
                and prior[0].document == evidence.document
                and prior[0].candidate == evidence.candidate
                and prior[0].context == evidence.context
            )
            revision = prior[1] if same else self._issued + 1
            service.register(CoverSourceSnapshot(
                scope=evidence.document, revision=revision,
                candidates=(evidence.candidate,), snapshot_complete=True,
            ))
            if not same:
                self._issued = revision
            self._known[evidence.document] = (evidence, revision)
            self._known.move_to_end(evidence.document)
            while len(self._known) > self._limit:
                self._known.popitem(last=False)
            return evidence.document


def register_exact_loan_supplier(
    host: Any, source_item: Loan, service: DocumentCoverService,
    evidence: VerifiedLoanSupplierCover, document: CoverDocumentScope,
) -> bool:
    """Recheck source identity, borrower and epochs at each visible GTK bind."""
    if (type(evidence) is not VerifiedLoanSupplierCover
            or type(service) is not DocumentCoverService
            or type(document) is not CoverDocumentScope
            or source_item is not evidence.source_item
            or evidence.document != document):
        return False
    # Do not call the display-only legacy resolver here: the visible row's
    # exact supplier source was already checked when its packet was built.
    live = _current(host, source_item)
    fresh = candidate(
        source_item.thumbnail_url, tier="catalog",
        source=source_item.source_provider,
        evidence_id=source_item.id, evidence_strength=0,
    )
    if (live != (document, evidence.context)
            or fresh is None or fresh != evidence.candidate):
        return False
    registry = getattr(host, "_loan_supplier_cover_registry", None)
    if registry is None:
        registry = LoanSupplierCoverRegistry()
        host._loan_supplier_cover_registry = registry
    if type(registry) is not LoanSupplierCoverRegistry:
        return False
    try:
        registry.register(service, evidence)
    except (TypeError, ValueError, RuntimeError):
        return False
    return True


def show_supplier_loan_cover(
    host: Any, loan: Loan, display: Callable[[], Loan],
) -> bool:
    """Render catalogue-tier N2 detail without changing show_loan's ABI."""
    if _current(host, loan) is None:
        return False
    presentation = display()
    evidence = exact_supplier_loan_cover(host, loan, displayed=presentation)
    if evidence is None:
        return False
    panel = getattr(getattr(host, "loan_page", None), "detail", None)
    if not callable(getattr(panel, "show_loan_local", None)):
        return False
    panel.show_loan_local(presentation, cover_evidence=evidence)
    return True


__all__ = [
    "VerifiedLoanSupplierCover", "LoanSupplierCoverRegistry",
    "exact_supplier_loan_cover", "register_exact_loan_supplier",
    "show_supplier_loan_cover",
]
