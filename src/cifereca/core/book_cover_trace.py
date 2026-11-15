"""N0 provenance-to-paint timeline for one exact Core book-cover scope (#6168).

A selected N1 cover is not a successful HTTP cache read; a physical cache
hit is not a GTK paint. Consumers explicitly publish the latter two facts
after checking media bytes and applying the decoded texture on the GTK main
context. This Core service never reads a URL, image, file or GTK object.

Exact `CoverDocumentScope` keys are internal. Exported observations contain
only closed vocabulary flags and generation/revision, never a title, path,
image URL, credential or account string. No cross-source documentary merge.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Literal

from .book_cover_cache import (
    CoverCacheToken, CoverDecisionCache, CoverDecisionChange,
    CoverSubscription,
)
from .book_cover_projection import CoverDocumentScope

CoverSurface = Literal["list", "detail"]
TraceStatus = Literal["unknown", "selected", "missing", "pending", "ambiguous"]


@dataclass(frozen=True, slots=True)
class CoverTraceSnapshot:
    """Closed, source-identifier-free observation for one exact scope."""

    revision: int
    generation: int
    status: TraceStatus
    evidence_present: bool = False
    resolved: bool = False
    media_cached: bool = False
    preview_cached: bool = False
    list_painted: bool = False
    detail_painted: bool = False
    invalidated: bool = False
    sequence: int = 0

    def public_fields(self) -> dict[str, int | bool | str]:
        return {
            "revision": self.revision,
            "generation": self.generation,
            "status": self.status,
            "evidence_present": self.evidence_present,
            "resolved": self.resolved,
            "media_cached": self.media_cached,
            "preview_cached": self.preview_cached,
            "list_painted": self.list_painted,
            "detail_painted": self.detail_painted,
            "invalidated": self.invalidated,
            "sequence": self.sequence,
        }


class CoverLifecycleTrace:
    """Thread-safe bounded N0 trace fed by N2 events, N3 probes, GTK paints.

    A generation change invalidates all previous media/paint confirmations,
    even when an old Image URL or SourceKey appears identical after ABA.
    N2 event `sequence` prevents out-of-order callbacks from rewinding a
    later invalidation. Neither the trace nor its public snapshot proves a
    loan OriginLink or modifies an entitlement.
    """

    def __init__(self, *, max_scopes: int = 2048):
        if type(max_scopes) is not int or not 1 <= max_scopes <= 20000:
            raise ValueError("invalid cover trace bound")
        self._limit = max_scopes
        self._lock = Lock()
        self._states: OrderedDict[CoverDocumentScope, CoverTraceSnapshot] = (
            OrderedDict()
        )

    def _record(
        self, scope: CoverDocumentScope, state: CoverTraceSnapshot,
    ) -> None:
        self._states[scope] = state
        self._states.move_to_end(scope)
        while len(self._states) > self._limit:
            self._states.popitem(last=False)

    def attach(
        self, cache: CoverDecisionCache, scope: CoverDocumentScope,
    ) -> CoverSubscription:
        """Subscribe to one source; cancellation stays owned by the caller."""
        if type(cache) is not CoverDecisionCache:
            raise TypeError("expected shared CoverDecisionCache")
        if type(scope) is not CoverDocumentScope:
            raise TypeError("expected exact CoverDocumentScope")
        return cache.subscribe(scope, self.consume)

    def consume(self, change: CoverDecisionChange) -> None:
        """N2 listener; tolerate reentrant, reordered, late notifications."""
        if type(change) is not CoverDecisionChange:
            raise TypeError("expected N2 cover event")
        if (type(change.scope) is not CoverDocumentScope
                or type(change.revision) is not int or change.revision < 0
                or type(change.generation) is not int or change.generation < 0
                or type(change.sequence) is not int or change.sequence <= 0
                or change.kind not in ("refresh", "resolved", "invalidated")):
            raise ValueError("invalid N2 cover event")
        if change.kind == "resolved":
            decision = change.decision
            if (decision is None or decision.scope != change.scope
                    or decision.revision != change.revision
                    or decision.status not in (
                        "selected", "missing", "pending", "ambiguous",
                    )):
                raise ValueError("invalid N2 resolved event")
        with self._lock:
            previous = self._states.get(change.scope)
            if previous is not None and (
                change.generation < previous.generation
                or change.sequence <= previous.sequence
            ):
                return
            if change.kind == "resolved":
                decision = change.decision
                status: TraceStatus = decision.status
                state = CoverTraceSnapshot(
                    revision=change.revision,
                    generation=change.generation,
                    sequence=change.sequence,
                    status=status,
                    evidence_present=decision.candidate_count > 0,
                    resolved=True,
                )
            else:
                state = CoverTraceSnapshot(
                    revision=change.revision,
                    generation=change.generation,
                    sequence=change.sequence,
                    status="unknown",
                    invalidated=change.kind == "invalidated",
                )
            self._record(change.scope, state)

    def note_media(
        self, token: CoverCacheToken, *,
        source_cached: bool, preview_cached: bool = False,
    ) -> bool:
        """Record a real N3 byte/signature probe for a selected N1 decision."""
        if type(token) is not CoverCacheToken:
            raise TypeError("expected exact N2 cache token")
        if type(source_cached) is not bool or type(preview_cached) is not bool:
            raise TypeError("media observations must be booleans")
        if preview_cached and not source_cached:
            raise ValueError("preview cache cannot exist without valid source")
        with self._lock:
            previous = self._states.get(token.scope)
            if (previous is None or previous.revision != token.revision
                    or previous.generation != token.generation
                    or previous.status != "selected" or not previous.resolved):
                return False
            state = CoverTraceSnapshot(
                revision=previous.revision,
                generation=previous.generation,
                status=previous.status,
                evidence_present=previous.evidence_present,
                resolved=True,
                media_cached=source_cached or previous.media_cached,
                preview_cached=preview_cached or previous.preview_cached,
                list_painted=previous.list_painted,
                detail_painted=previous.detail_painted,
                sequence=previous.sequence,
            )
            self._record(token.scope, state)
        return True

    def note_painted(
        self, token: CoverCacheToken, *, surface: CoverSurface,
    ) -> bool:
        """Only call after Gtk/Gdk accepted an actual image texture."""
        if type(token) is not CoverCacheToken:
            raise TypeError("expected exact N2 cache token")
        if surface not in ("list", "detail"):
            raise ValueError("unknown cover paint surface")
        with self._lock:
            previous = self._states.get(token.scope)
            if (previous is None or previous.revision != token.revision
                    or previous.generation != token.generation
                    or previous.status != "selected"
                    or not previous.resolved or not previous.media_cached):
                return False
            state = CoverTraceSnapshot(
                revision=previous.revision,
                generation=previous.generation,
                status=previous.status,
                evidence_present=previous.evidence_present,
                resolved=True,
                media_cached=True,
                preview_cached=previous.preview_cached,
                list_painted=(
                    surface == "list" or previous.list_painted
                ),
                detail_painted=(
                    surface == "detail" or previous.detail_painted
                ),
                sequence=previous.sequence,
            )
            self._record(token.scope, state)
        return True

    def snapshot(self, scope: CoverDocumentScope) -> CoverTraceSnapshot | None:
        if type(scope) is not CoverDocumentScope:
            raise TypeError("expected exact source")
        with self._lock:
            state = self._states.get(scope)
            if state is not None:
                self._states.move_to_end(scope)
            return state

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"tracked_sources": len(self._states)}


__all__ = ["CoverTraceSnapshot", "CoverLifecycleTrace"]
