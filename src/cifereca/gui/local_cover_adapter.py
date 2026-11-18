"""Adapt exact Core local cover evidence to shared N2 snapshots (#6168).

Core owns the document/Artifact/Placement/member proof. This adapter owns
ONLY a monotone in-memory publication epoch for a changed Core snapshot.
Neither cache entries nor revocation rights are partitioned by GTK profile:
N2 remains exact-document scoped, N3 remains physical-resource shared.

Register on visible GTK cell/detail admission, NOT for all books in a
30,000-row Library. No disk, ZIP, HTTP, provider, rights or title lookup.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from threading import Lock

from ..core.book_cover_projection import CoverDocumentScope
from ..core.local_cover_evidence import VerifiedLocalCoverEvidence
from ..core.notice_c_local_origin_projection import VerifiedLocalPublicCLink
from .document_cover_service import CoverSourceSnapshot, DocumentCoverService


class LocalCoverEvidenceAdapter:
    """Bind Core-owned evidence to one monotone N2 epoch per changed source.

    A-B-A with the same book key produces revisions 1-2-3, not an old
    lease or an accidental collision on image URL / SHA alone. The registry
    is bounded and can be evicted independently from N2; its process-wide
    issuance counter keeps revisions increasing even after eviction.
    """

    def __init__(self, *, max_sources: int = 4096):
        if type(max_sources) is not int or not 1 <= max_sources <= 20000:
            raise ValueError("invalid local cover evidence bound")
        self._limit = max_sources
        self._lock = Lock()
        self._issued = 0
        self._closed = False
        self._audit_binding = None
        self._known: OrderedDict[
            CoverDocumentScope, tuple[VerifiedLocalCoverEvidence, int]
        ] = OrderedDict()

    def register(
        self, service: DocumentCoverService,
        facts: VerifiedLocalCoverEvidence,
    ) -> CoverDocumentScope:
        """Publish one exact packet, without a second Core/library read.

        The supplying Core library worker already verified identity,
        Artifact and Placement and populated the immutable packet. A GTK
        cell cannot synthesize this data from its URL, key or filename.
        """
        if type(service) is not DocumentCoverService:
            raise TypeError("expected shared document cover service")
        if type(facts) is not VerifiedLocalCoverEvidence:
            raise TypeError("expected Core-attested local cover facts")
        with self._lock:
            if self._closed:
                raise RuntimeError("local cover adapter closed")
            old = self._known.get(facts.scope)
            if old is not None and old[0] == facts:
                revision = old[1]
            else:
                revision = self._issued + 1

            embedded = facts.embedded
            if embedded is not None:
                embedded = replace(
                    embedded,
                    resource=replace(
                        embedded.resource, source_revision=revision,
                    ),
                )
            snapshot = CoverSourceSnapshot(
                scope=facts.scope, revision=revision,
                candidates=facts.candidates,
                embedded=embedded,
                verified_epub_path=(
                    facts.verified_path if embedded is not None else None
                ),
            )
            # Do not advance the registry if the N2 service is closed or
            # rejects a malformed snapshot. No silent URL fallback here.
            service.register(snapshot)
            if old is None or old[0] != facts:
                self._issued = revision
            self._known[facts.scope] = (facts, revision)
            self._known.move_to_end(facts.scope)
            while len(self._known) > self._limit:
                self._known.popitem(last=False)
            return facts.scope

    def bind_audit_observer(self, owner, covers: DocumentCoverService) -> None:
        """Bind on the GUI owner thread; close detaches only this binding."""
        with self._lock:
            if self._closed or self._audit_binding is not None:
                raise RuntimeError("local cover observer already bound or closed")
            def observe(packet):
                return self.observe_audit_packet(covers, packet)
            self._audit_binding = (owner, observe)
            owner._documentary_cover_observer = observe

    def observe_audit_packet(self, service: DocumentCoverService, packet):
        """Exact existing publication only; never register a new snapshot."""
        with self._lock:
            if self._closed:
                return None
        probe = getattr(service, "observe_cover_for_audit", None)
        if not callable(probe):
            return None
        if type(packet) is VerifiedLocalPublicCLink:
            # Core validated the retained C link/epoch before this call.
            result = probe(packet.document, expected_revision=packet.epoch)
            with self._lock:
                if self._closed:
                    return None
        elif type(packet) is VerifiedLocalCoverEvidence:
            with self._lock:
                known = self._known.get(packet.scope)
                if self._closed or known is None or known[0] != packet:
                    return None
            result = probe(packet.scope, expected_revision=known[1])
            with self._lock:
                if self._closed or self._known.get(packet.scope) != known:
                    return None
        else:
            return None
        return result.n2_status, result.n2_tier, result.n3_status

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"sources": len(self._known)}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._known.clear()
            binding, self._audit_binding = self._audit_binding, None
            if binding is not None:
                owner, observer = binding
                if getattr(owner, "_documentary_cover_observer", None) is observer:
                    owner._documentary_cover_observer = None


__all__ = ["LocalCoverEvidenceAdapter"]
