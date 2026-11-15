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
import time
from pathlib import Path
from threading import Lock, RLock
from typing import Literal

from ..core.book_cover_cache import CoverDecisionCache, CoverCacheToken
from ..core.book_cover_projection import (
    CoverDocumentScope, ResolvedBookCover, resolve_book_cover,
)
from ..core.covers import CoverCandidate, candidate
from ..core.epub_cover_source import AttestedEmbeddedCover
from ..core.epub_cover_failure_qualification import EpubCoverPhysicalAssessment
from ..epub_metadata import cover_cache_dir
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
            # A public/provisional URL cannot assert embedded. Only an
            # Artifact+Placement attested by Core may supply this tier in
            # the separate immutable embedded field below.
            if raw.tier not in ("origin", "catalog"):
                raise ValueError("embedded cover requires exact Core proof")
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
            # A cache filename is just physical bytes: it must not allow
            # an untrusted file URI, another cache directory or a
            # catalogue URL to masquerade as Core's selected embedded
            # source. No image bytes are read during this N1 validation.
            proof = self.embedded.candidate
            resource = self.embedded.resource
            if (type(proof) is not CoverCandidate
                    or proof.source != "epub"
                    or proof.evidence_id != self.scope.artifact_id
                    or proof.url != (
                        cover_cache_dir() / resource.cache_name
                    ).resolve().as_uri()
                    or candidate(
                        proof.url, tier="embedded", source=proof.source,
                        evidence_id=proof.evidence_id,
                        evidence_strength=proof.evidence_strength,
                    ) != proof):
                raise ValueError("unmatched embedded EPUB provenance")
        elif self.verified_epub_path is not None:
            raise ValueError("unattached EPUB path")


@dataclass(frozen=True, slots=True)
class _QualifiedFallbackLease:
    revision: int
    url: str
    token: CoverCacheToken
    expires_at: float


class CoverReadRequest:
    """Cancelable read with no extra network work after a recycled GTK bind."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active = True
        self._child: CoverRequest | None = None
        # Authoritative N2 selected URI, never inferred from physical N3.
        self._source_uri: str | None = None

    @property
    def source_uri(self) -> str | None:
        return self._source_uri

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
        fallback_ttl: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not isinstance(loader, CoverLoader):
            raise TypeError("expected shared CoverLoader")
        if decisions is not None and type(decisions) is not CoverDecisionCache:
            raise TypeError("expected exact N2 decision cache")
        if type(max_documents) is not int or not 1 <= max_documents <= 20000:
            raise ValueError("invalid book cover cache bound")
        if (type(fallback_ttl) not in (int, float)
                or not 0 < fallback_ttl <= 300
                or not float("-inf") < fallback_ttl < float("inf")
                or not callable(clock)):
            raise ValueError("invalid qualified fallback lifetime")
        self.loader = loader
        self.decisions = decisions if decisions is not None else CoverDecisionCache()
        self._max_documents = max_documents
        self._fallback_ttl = float(fallback_ttl)
        self._clock = clock
        # Temporary N2 requalification, never a physical N3 media cache.
        self._qualified_fallback: dict[CoverDocumentScope, _QualifiedFallbackLease] = {}
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
            self._qualified_fallback.pop(snapshot.scope, None)
            if previous is not None:
                self.decisions.invalidate(snapshot.scope, cause="evidence_changed")
            while len(self._snapshots) > self._max_documents:
                old_scope, _ = self._snapshots.popitem(last=False)
                self._qualified_fallback.pop(old_scope, None)
                self.decisions.invalidate(old_scope, cause="evidence_changed")

    def apply_qualified_epub_fallback(
        self,
        document: CoverDocumentScope,
        assessment: EpubCoverPhysicalAssessment,
        *,
        expected_token: CoverCacheToken,
    ) -> ResolvedBookCover | None:
        """CAS N2 to an admitted URL after a verified, definitive N3 failure.

        This is an explicit Core-to-N2 transition. The caller MUST run the
        bounded EPUB physical qualifier in a WORKER after N3 failed. Neither
        an absent cache image nor a N3 Future(None) is sufficient evidence.
        N2 source identity is checked before any new decision is published.

        If no *unique* admitted origin/catalogue candidate exists, leave
        embedded N2 unchanged. An ambiguous fallback never downloads.
        """
        if (type(document) is not CoverDocumentScope
                or type(assessment) is not EpubCoverPhysicalAssessment
                or type(expected_token) is not CoverCacheToken):
            raise TypeError("expected exact cover scope, failure and token")
        if (assessment.state != "definitive_unusable"
                or assessment.code not in (
                    "member_missing_or_ambiguous", "member_unusable",
                    "image_digest_mismatch",
                )):
            return None
        with self._lock:
            if self._closed:
                return None
            snapshot = self._snapshots.get(document)
            if (snapshot is None or not snapshot.snapshot_complete
                    or snapshot.embedded is None
                    or assessment.resource != snapshot.embedded.resource
                    or snapshot.revision != expected_token.revision
                    or document != expected_token.scope):
                return None
            source_uri = snapshot.embedded.candidate.url
            if self.decisions.selected_token(
                document, revision=snapshot.revision, uri=source_uri,
            ) != expected_token:
                return None

            fallback = resolve_book_cover(
                document, snapshot.candidates,
                revision=snapshot.revision, snapshot_complete=True,
            )
            if (fallback.status != "selected"
                    or fallback.tier not in ("origin", "catalog")):
                return None

            # N2 CAS revokes the old selected EPUB generation and publishes
            # the separately attested fallback URL. Cached URL bytes in N3
            # do not participate in this choice or prove a book identity.
            token = self.decisions.begin(
                document, revision=snapshot.revision,
            )
            if not self.decisions.publish_resolved(token, fallback):
                return None
            self._qualified_fallback[document] = _QualifiedFallbackLease(
                snapshot.revision, fallback.uri, token,
                self._clock() + self._fallback_ttl,
            )
            return fallback

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
                lease = self._qualified_fallback.get(document)
                if lease is not None:
                    live = self.decisions.selected_token(
                        document, revision=lease.revision, uri=lease.url,
                    )
                    if (snapshot.revision != lease.revision
                            or live != lease.token):
                        self._qualified_fallback.pop(document, None)
                    elif self._clock() >= lease.expires_at:
                        # Reconsider the original EPUB N1 priority after a
                        # bounded lease. Repair of N3 can restore the image;
                        # no public URL may permanently displace it.
                        self._qualified_fallback.pop(document, None)
                        self.decisions.invalidate(
                            document, cause="evidence_changed",
                        )
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
        # The return handle exposes the N2 source before any synchronous
        # N3 cache-hit callback can be painted on GTK's idle queue.
        handle._source_uri = decision.uri

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
            self._qualified_fallback.clear()


__all__ = [
    "CoverSourceSnapshot", "CoverReadRequest", "DocumentCoverService",
]
