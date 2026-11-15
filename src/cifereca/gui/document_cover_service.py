"""DocumentRef-first shared cover gateway for five GTK views (P1 #6168).

Producers register exact source evidence once. List/detail ask ONLY for a
DocumentRef (CoverDocumentScope) and a variant. N2 caches the documentary
source decision, while N3 separately supplies validated image bytes and
previews via the existing CoverLoader / EPUB physical materializer.

This bridge does not interpret provider-specific schemas, join books by
title/ISBN/URL, authorize a loan, or start catalogue/EPUB scans. Callbacks
are thread-agnostic: GTK callers must dispatch and bind-check on main.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from typing import Literal

from ..core.book_cover_cache import CoverDecisionCache
from ..core.book_cover_projection import CoverDocumentScope
from ..core.covers import CoverCandidate, candidate
from ..core.epub_cover_source import AttestedEmbeddedCover
from .cover_loader import CoverLoader, CoverRequest


CoverVariant = Literal["list", "detail"]
_MAX_EVIDENCE = 4096


@dataclass(frozen=True, slots=True)
class CoverSourceSnapshot:
    """Producer-owned immutable set of proofs for exactly ONE book reference."""

    scope: CoverDocumentScope
    revision: int
    candidates: tuple[CoverCandidate, ...] = ()
    embedded: AttestedEmbeddedCover | None = None
    verified_epub_path: Path | None = None
    snapshot_complete: bool = True

    def __post_init__(self) -> None:
        if type(self.scope) is not CoverDocumentScope:
            raise TypeError("expected exact DocumentRef")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("invalid source revision")
        if type(self.snapshot_complete) is not bool:
            raise TypeError("source completeness must be explicit")
        if (type(self.candidates) is not tuple
                or len(self.candidates) > _MAX_EVIDENCE):
            raise ValueError("invalid cover source inventory")
        for raw in self.candidates:
            if type(raw) is not CoverCandidate:
                raise TypeError("invalid source candidate")
            checked = candidate(
                raw.url, tier=raw.tier, source=raw.source,
                evidence_id=raw.evidence_id,
                evidence_strength=raw.evidence_strength,
            )
            if checked is None or checked != raw:
                raise ValueError("unadmitted cover source candidate")
        if self.embedded is not None:
            if (type(self.embedded) is not AttestedEmbeddedCover
                    or self.scope.kind != "placement"
                    or self.embedded.resource.scope != self.scope
                    or self.embedded.resource.source_revision != self.revision
                    or self.embedded.candidate.tier != "embedded"):
                raise ValueError("unmatched EPUB source evidence")
            # The owning Core service must supply a verified local Placement,
            # not a path inferred from catalogue/ISBN or GTK.
            source = self.verified_epub_path
            if (not isinstance(source, Path) or not source.is_absolute()
                    or source.suffix.lower() not in (".epub", ".kepub")):
                raise ValueError("expected exact verified EPUB Placement path")
        elif self.verified_epub_path is not None:
            raise ValueError("unattached EPUB path")


class CoverReadRequest:
    """Cancelable read with no extra network work after a recycled GTK bind."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active = True
        self._child: CoverRequest | None = None

    def cancel(self) -> None:
        with self._lock:
            self._active = False
            child = self._child
        if child is not None:
            child.cancel()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def _attach(self, child: CoverRequest) -> None:
        with self._lock:
            self._child = child
            active = self._active
        if not active:
            child.cancel()


class DocumentCoverService:
    """Read API: get_cover(DocumentRef, variant, callback).

    Registration and cache invalidation are Core producer responsibilities.
    N2's exact CAS token is checked AGAIN after any N3 worker callback, so
    stale A->B->A results cannot repaint a recycled list cell or detail pane.
    """

    def __init__(
        self, loader: CoverLoader, *,
        decisions: CoverDecisionCache | None = None,
        max_documents: int = 4096,
    ):
        if not isinstance(loader, CoverLoader):
            raise TypeError("expected shared CoverLoader")
        if decisions is not None and type(decisions) is not CoverDecisionCache:
            raise TypeError("expected exact N2 decision cache")
        if type(max_documents) is not int or not 1 <= max_documents <= 20000:
            raise ValueError("invalid book cover cache bound")
        self.loader = loader
        self.decisions = decisions if decisions is not None else CoverDecisionCache()
        self._max_documents = max_documents
        self._lock = RLock()
        self._snapshots: OrderedDict[CoverDocumentScope, CoverSourceSnapshot] = OrderedDict()
        self._resolving: set[CoverDocumentScope] = set()
        self._closed = False

    def register(self, snapshot: CoverSourceSnapshot) -> None:
        """Install only a newer exact source snapshot; no HTTP or EPUB I/O."""
        if type(snapshot) is not CoverSourceSnapshot:
            raise TypeError("expected attested source inventory")
        with self._lock:
            if self._closed:
                raise RuntimeError("cover service is closed")
            previous = self._snapshots.get(snapshot.scope)
            if previous is not None:
                if snapshot.revision < previous.revision:
                    raise ValueError("stale cover source revision")
                if snapshot.revision == previous.revision:
                    if snapshot != previous:
                        raise ValueError("changed cover evidence requires revision")
                    self._snapshots.move_to_end(snapshot.scope)
                    return
            self._snapshots[snapshot.scope] = snapshot
            self._snapshots.move_to_end(snapshot.scope)
            if previous is not None:
                self.decisions.invalidate(snapshot.scope, cause="evidence_changed")
            while len(self._snapshots) > self._max_documents:
                old_scope, _ = self._snapshots.popitem(last=False)
                self.decisions.invalidate(old_scope, cause="evidence_changed")

    def get_cover(
        self,
        document: CoverDocumentScope, variant: CoverVariant,
        callback: Callable[[Path | None], None], *,
        width: int = 180, height: int = 270,
    ) -> CoverReadRequest:
        """Use N2 HIT first; only N3 fetch/extraction follows an N1 selection.

        A missing physical EPUB image never makes the already-attested
        embedded source lose its N1 preference to a cached catalogue URL.
        A failed N3 recovery is reported as unavailable, not invented
        documentary 'missing'; bounded remote fallback remains a separate
        policy to implement under #6168.
        """
        if type(document) is not CoverDocumentScope:
            raise TypeError("expected exact DocumentRef")
        if variant not in ("list", "detail"):
            raise ValueError("unknown cover variant")
        if not callable(callback):
            raise TypeError("expected cover callback")
        if (type(width) is not int or type(height) is not int
                or not 1 <= width <= 2048 or not 1 <= height <= 2048):
            raise ValueError("invalid preview dimensions")
        handle = CoverReadRequest()
        with self._lock:
            if self._closed:
                raise RuntimeError("cover service is closed")
            snapshot = self._snapshots.get(document)
            if snapshot is None:
                decision = None
            else:
                self._snapshots.move_to_end(document)
                decision = self.decisions.peek(document, revision=snapshot.revision)
                if decision is None and document not in self._resolving:
                    self._resolving.add(document)
                    try:
                        token = self.decisions.begin(
                            document, revision=snapshot.revision,
                        )
                        proofs = snapshot.candidates + (
                            (snapshot.embedded.candidate,)
                            if snapshot.embedded is not None else ()
                        )
                        self.decisions.resolve_and_publish(
                            token, proofs,
                            snapshot_complete=snapshot.snapshot_complete,
                        )
                        decision = self.decisions.peek(
                            document, revision=snapshot.revision,
                        )
                    finally:
                        self._resolving.discard(document)
            selected_token = (
                self.decisions.selected_token(
                    document, revision=snapshot.revision, uri=decision.uri,
                )
                if snapshot is not None and decision is not None
                and decision.status == "selected" else None
            )
        if snapshot is None or decision is None or selected_token is None:
            callback(None)
            return handle

        def delivered(path: Path | None) -> None:
            if not handle.active:
                return
            with self._lock:
                valid = (
                    not self._closed
                    and self._snapshots.get(document) is snapshot
                    and self.decisions.selected_token(
                        document, revision=snapshot.revision, uri=decision.uri,
                    ) == selected_token
                )
            if valid and handle.active:
                callback(path)

        if decision.tier == "embedded":
            embedded = snapshot.embedded
            if (embedded is None or embedded.candidate.url != decision.uri
                    or snapshot.verified_epub_path is None):
                delivered(None)
                return handle
            child = self.loader.load_exact_epub(
                embedded.resource,
                source_path=snapshot.verified_epub_path,
                callback=delivered,
                **({"width": width, "height": height} if variant == "list" else {}),
            )
        elif decision.tier in ("origin", "catalog"):
            if variant == "list":
                child = self.loader.load_preview(
                    decision.uri, delivered, width=width, height=height,
                )
            else:
                child = self.loader.load(decision.uri, delivered)
        else:
            delivered(None)
            return handle
        handle._attach(child)
        return handle

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._snapshots.clear()
            self._resolving.clear()


__all__ = [
    "CoverSourceSnapshot", "CoverReadRequest", "DocumentCoverService",
]
