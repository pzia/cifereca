"""Exact source-occurrence History cover admission into shared N2 (#6168).

The original provider HistoryItem owns its own direct thumbnail, not the
thumbnail of a homonymous title/ISBN or a previous loan. Retained visual
fallbacks from HistoryCoverResolver are NEVER upgraded into documentary
evidence. All N3 physical bytes remain shared across cifereca profiles.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import HistoryItem
from ..core.book_cover_projection import CoverDocumentScope
from ..core.covers import candidate
from .document_cover_service import CoverSourceSnapshot, DocumentCoverService
from .history_cover_decision_bridge import (
    HistoryCoverDecisionBridge, exact_history_cover_scope,
)


@dataclass(frozen=True, slots=True)
class VerifiedHistoryCoverEvidence:
    """One exact source occurrence and its window-local revision/epochs."""

    source_item: HistoryItem
    snapshot: CoverSourceSnapshot
    account_epoch: int
    source_epoch: int
    history_revision: int

    @property
    def document(self) -> CoverDocumentScope:
        return self.snapshot.scope


def _context(window: Any, item: HistoryItem):
    service = getattr(window, "service", None)
    identity = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    account_epoch = getattr(service, "_account_context_epoch", None)
    source_epoch = getattr(service, "_source_context_epoch", None)
    revision = getattr(window, "_history_cover_revision", None)
    live = getattr(window, "history_objects", None)
    if (type(item) is not HistoryItem
            or type(identity) is not str or not identity
            or type(account_epoch) is not int or account_epoch < 0
            or type(source_epoch) is not int or source_epoch < 0
            or type(revision) is not int or revision < 1
            or type(live) is not dict
            or live.get(item.id) is not item):
        return None
    scope = exact_history_cover_scope(item, identity_id=identity)
    if scope is None:
        return None
    return scope, account_epoch, source_epoch, revision


def exact_history_document_evidence(
    window: Any, item: HistoryItem,
) -> VerifiedHistoryCoverEvidence | None:
    """Adapt only the History source's own attested thumbnail to a ref.

    No browsing or provider I/O on History selection: reuse the existing
    HistoryCoverDecisionBridge source revision and exact N1 choice.
    """
    context = _context(window, item)
    if context is None:
        return None
    scope, account_epoch, source_epoch, history_revision = context
    direct = candidate(
        item.thumbnail_url, tier="catalog", source=item.source_provider,
        evidence_id=item.id,
    )
    if direct is None:
        return None

    bridge = getattr(window, "_history_cover_decisions", None)
    if bridge is None:
        bridge = HistoryCoverDecisionBridge()
        window._history_cover_decisions = bridge
    if type(bridge) is not HistoryCoverDecisionBridge:
        return None
    decision = bridge.resolve(
        item, identity_id=scope.identity_id,
        account_epoch=account_epoch,
        history_revision=history_revision,
    )
    if (decision is None or decision.status != "selected"
            or decision.scope != scope or decision.uri != direct.url
            or decision.tier != "catalog"):
        return None
    try:
        snapshot = CoverSourceSnapshot(
            scope=scope, revision=decision.revision,
            candidates=(direct,), snapshot_complete=True,
        )
    except (TypeError, ValueError):
        return None
    return VerifiedHistoryCoverEvidence(
        source_item=item, snapshot=snapshot,
        account_epoch=account_epoch, source_epoch=source_epoch,
        history_revision=history_revision,
    )


def register_exact_history_document(
    window: Any,
    source_item: HistoryItem,
    service: DocumentCoverService,
    evidence: VerifiedHistoryCoverEvidence,
    document: CoverDocumentScope,
) -> bool:
    """Admit one currently selected exact History occurrence on GTK bind.

    Re-check the original object, source/account epochs and the immutable
    source URL at paint-request time. No N3 hit can grant documentary rights.
    """
    if (type(service) is not DocumentCoverService
            or type(evidence) is not VerifiedHistoryCoverEvidence
            or type(document) is not CoverDocumentScope
            or source_item is not evidence.source_item
            or evidence.snapshot.scope != document):
        return False
    context = _context(window, source_item)
    if context is None:
        return False
    scope, account_epoch, source_epoch, history_revision = context
    if (scope != document
            or account_epoch != evidence.account_epoch
            or source_epoch != evidence.source_epoch
            or history_revision != evidence.history_revision):
        return False
    direct = candidate(
        source_item.thumbnail_url, tier="catalog",
        source=source_item.source_provider, evidence_id=source_item.id,
    )
    if (direct is None
            or evidence.snapshot.candidates != (direct,)
            or not evidence.snapshot.snapshot_complete):
        return False
    bridge = getattr(window, "_history_cover_decisions", None)
    if type(bridge) is not HistoryCoverDecisionBridge:
        return False
    decision = bridge.resolve(
        source_item, identity_id=scope.identity_id,
        account_epoch=account_epoch, history_revision=history_revision,
    )
    if (decision is None or decision.status != "selected"
            or decision.scope != document
            or decision.revision != evidence.snapshot.revision
            or decision.uri != direct.url):
        return False
    try:
        service.register(evidence.snapshot)
    except (ValueError, TypeError, RuntimeError):
        return False
    return True


__all__ = [
    "VerifiedHistoryCoverEvidence", "exact_history_document_evidence",
    "register_exact_history_document",
]
