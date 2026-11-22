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

from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass, replace
import math
import time
from time import perf_counter
from pathlib import Path
from threading import Lock, RLock
from typing import Literal
from weakref import WeakSet

from ..core.book_cover_cache import (
    CoverCacheToken, CoverDecisionCache, CoverSubscription,
)
from ..core.book_cover_projection import (
    CoverDocumentScope, ResolvedBookCover,
)
from ..core.book_cover_trace import CoverLifecycleTrace, CoverTraceSnapshot
from ..core.covers import CoverCandidate, candidate
from ..core.epub_cover_source import AttestedEmbeddedCover
from ..core.epub_cover_failure_qualification import (
    EpubCoverPhysicalAssessment, qualify_failed_epub_cover,
)
from ..epub_metadata import cover_cache_dir, valid_cached_cover_path
from .cover_loader import CoverLoader, CoverRequest


CoverVariant = Literal["list", "detail"]
_MAX_EVIDENCE = 4096
_N2_LATENCY_WINDOW = 1024


@dataclass(frozen=True, slots=True)
class CoverAuditObservation:
    """Current exact N2 decision and independent physical N3 observation.

    N3 'not_validated' combines absence, invalidity and unreadability; it
    never revokes N2. A signature check is not a full image decode.
    """

    n2_status: str = "unknown"
    n2_tier: str = ""
    n3_status: str = "unknown"


@dataclass(frozen=True, slots=True)
class CoverSourceSnapshot:
    """Producer-owned immutable set of proofs for exactly ONE book reference."""

    scope: CoverDocumentScope
    revision: int
    candidates: tuple[CoverCandidate, ...] = ()
    embedded: AttestedEmbeddedCover | None = None
    verified_epub_path: Path | None = None
    snapshot_complete: bool = True
    # Only the durable canonical-C cover index can attest a multi-source
    # same-edition relation for deterministic catalogue tie-breaking.
    attested_book_identity: str = ""

    def __post_init__(self) -> None:
        if type(self.scope) is not CoverDocumentScope:
            raise TypeError("expected exact DocumentRef")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("invalid source revision")
        if type(self.snapshot_complete) is not bool:
            raise TypeError("source completeness must be explicit")
        if (type(self.attested_book_identity) is not str
                or (self.attested_book_identity and (
                    not self.scope.source_key.startswith(
                        "notice-c:canonical-cover:v1:"
                    )
                    or self.attested_book_identity != self.scope.source_key
                ))):
            raise ValueError("invalid canonical cover relation")
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
            if self.scope.kind == "public_catalogue" and raw.tier != "catalog":
                raise ValueError("public catalogue scope requires direct catalogue evidence")
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
    failed_catalogue_urls: tuple[str, ...] = ()


class CoverReadRequest:
    """Cancelable read with no extra network work after a recycled GTK bind."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active = True
        self._child: CoverRequest | None = None
        # Authoritative N2 selected URI, never inferred from physical N3.
        self._source_uri: str | None = None
        self._selection_token: CoverCacheToken | None = None
        # Recheck the EXACT N2 selection at GTK idle-paint time, not just
        # at the N3 worker callback (which may queue an idle beforehand).
        self._is_selected: Callable[[], bool] | None = None
        self._paint_receipt: tuple[Path, Callable[[], bool]] | None = None
        self._cancel_source_subscription: Callable[[], None] | None = None

    @property
    def source_uri(self) -> str | None:
        return self._source_uri

    @property
    def still_current(self) -> bool:
        with self._lock:
            if not self._active:
                return False
            check = self._is_selected
        if check is None:
            # Pending/unselected reads can still deliver a neutral missing
            # placeholder. The generation guard rejects recycled widgets.
            return True
        try:
            return check() is True
        except Exception:
            return False

    def _offer_paint_receipt(self, path: Path, receipt: Callable[[], bool]) -> None:
        with self._lock:
            if self._active:
                self._paint_receipt = path, receipt

    def note_texture_applied(self, path: Path) -> bool:
        """Consume the receipt for the exact image this request delivered."""
        with self._lock:
            receipt = self._paint_receipt
            if not self._active or receipt is None or receipt[0] != path:
                return False
            self._paint_receipt = None
        return receipt[1]()

    def cancel(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            self._paint_receipt = None
            child = self._child
            self._child = None
            unsubscribe = self._cancel_source_subscription
            self._cancel_source_subscription = None
        if child is not None:
            child.cancel()
        if unsubscribe is not None:
            unsubscribe()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def _attach(self, child: CoverRequest) -> None:
        with self._lock:
            active = self._active
            if active:
                self._child = child
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
        cached_source_reader: Callable[[CoverDocumentScope], CoverSourceSnapshot | None] | None = None,
        cached_source_scope: Callable[[CoverDocumentScope], bool] | None = None,
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
        if cached_source_reader is not None and not callable(cached_source_reader):
            raise TypeError("cached cover source reader must be callable")
        if (cached_source_scope is not None and
                (cached_source_reader is None or not callable(cached_source_scope))):
            raise TypeError("cached source scope requires a callable reader")
        self.loader = loader
        self.decisions = decisions if decisions is not None else CoverDecisionCache()
        self._max_documents = max_documents
        self._trace = CoverLifecycleTrace(max_scopes=max_documents)
        self._trace_subscriptions: dict[CoverDocumentScope, CoverSubscription] = {}
        self._fallback_ttl = float(fallback_ttl)
        self._clock = clock
        # Temporary N2 requalification, never a physical N3 media cache.
        self._qualified_fallback: dict[CoverDocumentScope, _QualifiedFallbackLease] = {}
        self._lock = RLock()
        self._snapshots: OrderedDict[CoverDocumentScope, CoverSourceSnapshot] = OrderedDict()
        # N2 performance counters, not document provenance. Never export
        # SourceKey, profile, credential, URL or private filesystem path.
        self._n2_recent = {outcome: deque(maxlen=_N2_LATENCY_WINDOW) for outcome in (
            "hit", "source_unknown", "below_floor", "resolution_miss",
        )}
        self._stats = {
            "n2_hit": 0, "n2_miss": 0, "n2_source_unknown": 0,
            "n2_resolution_miss": 0, "n2_register_new": 0,
            "n2_register_changed": 0, "n2_register_reused": 0,
            "n2_source_refresh_scheduled": 0,
        }        # N2 documentary source events for currently bound GTK consumers.
        # A newly admitted Notice C value can update an already visible
        # placeholder without a new Search selection or provider request.
        self._source_subscriptions: dict[
            CoverDocumentScope,
            dict[int, tuple[CoverReadRequest, Callable[[], None]]],
        ] = {}
        # Track even N3-only reads and consumers refused by the bounded
        # N2 subscription port. Weak ownership does not retain dead views.
        self._live_reads: WeakSet[CoverReadRequest] = WeakSet()
        self._source_subscription_id = 0
        self._source_subscription_count = 0
        self._resolving: set[CoverDocumentScope] = set()
        # Optional Core-only read-through on an exact N2 source MISS. The
        # reader must return cached, already-admitted documentary evidence;
        # it runs in a bounded worker, never inside a GTK widget or N3.
        self._cached_source_reader = cached_source_reader
        self._cached_source_scope = cached_source_scope
        self._read_pool = (
            ThreadPoolExecutor(max_workers=2, thread_name_prefix="cifereca-cover-source")
            if cached_source_reader is not None else None
        )
        self._read_active: set[CoverDocumentScope] = set()
        # A newer, producer-attested Notice C epoch may supersede an N2 HIT.
        # This is a targeted, bounded background re-read, not a per-rebind
        # source poll or a second documentary authority in GTK.
        self._refresh_active: set[CoverDocumentScope] = set()
        self._refresh_targets: dict[CoverDocumentScope, int] = {}
        self._read_waiters: dict[
            CoverDocumentScope, list[tuple[CoverReadRequest, Callable[[], None]]]
        ] = {}
        self._read_cooldown: OrderedDict[CoverDocumentScope, float] = OrderedDict()
        # A failed N3 materialization is only a request for worker-side
        # physical assessment, NEVER evidence of a missing documentary EPUB.
        # One bounded qualifier per exact (DocumentRef, source revision).
        self._qualify_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="cifereca-cover-qualifier",
        )
        self._qualify_active: set[tuple[CoverDocumentScope, int]] = set()
        # Physical single-flight is shared, but EACH current list/detail
        # consumer needs its own GTK rebind after the exact N2 transition.
        self._qualify_waiters: dict[
            tuple[CoverDocumentScope, int],
            list[tuple[CoverReadRequest, Callable[[], None]]],
        ] = {}
        self._qualify_cooldown: OrderedDict[
            tuple[CoverDocumentScope, int], float
        ] = OrderedDict()
        self._closed = False

    @staticmethod
    def _snapshot_evidence(snapshot: CoverSourceSnapshot) -> CoverSourceSnapshot:
        """Own the N1 source and every mutable frozen receipt before N2 admission.

        A producer can mutate a dataclass with object.__setattr__ after
        publishing it. N2 keys, the selected N1 URL and the EPUB resource
        must keep the exact revision and coordinates admitted at that time.
        """
        snapshot.__post_init__()
        scope = CoverDecisionCache._snapshot_scope(snapshot.scope)
        embedded = snapshot.embedded
        if embedded is not None:
            from ..core.epub_cover_materialization import ExactEpubCoverResource
            if type(embedded.resource) is not ExactEpubCoverResource:
                raise TypeError("invalid embedded EPUB resource")
            embedded = AttestedEmbeddedCover(
                candidate=replace(embedded.candidate),
                resource=replace(
                    embedded.resource,
                    scope=CoverDecisionCache._snapshot_scope(embedded.resource.scope),
                ),
            )
        return replace(
            snapshot, scope=scope,
            candidates=tuple(replace(value) for value in snapshot.candidates),
            embedded=embedded,
        )

    def register(self, snapshot: CoverSourceSnapshot) -> None:
        """Install a newer exact source snapshot; notify viewers after unlock."""
        self._register_snapshot(snapshot, only_if_absent=False)

    def _register_snapshot(
        self, snapshot: CoverSourceSnapshot, *, only_if_absent: bool,
    ) -> None:
        """CAS on presence for Core read-through, never notify under N2 lock.

        A concurrent stronger producer can win before the worker's persisted
        source returns. Its live value must not be replaced by a stale worker.
        """
        if type(snapshot) is not CoverSourceSnapshot:
            raise TypeError("expected attested source inventory")
        snapshot = self._snapshot_evidence(snapshot)
        with self._lock:
            if self._closed:
                raise RuntimeError("cover service is closed")
            previous = self._snapshots.get(snapshot.scope)
            if only_if_absent and previous is not None:
                return
            if previous is not None:
                if snapshot.revision < previous.revision:
                    raise ValueError("stale cover source revision")
                if snapshot.revision == previous.revision:
                    if snapshot != previous:
                        raise ValueError("changed cover evidence requires revision")
                    self._snapshots.move_to_end(snapshot.scope)
                    self._stats["n2_register_reused"] += 1
                    return
            self._stats[
                "n2_register_changed" if previous is not None
                else "n2_register_new"
            ] += 1
            self._snapshots[snapshot.scope] = snapshot
            self._snapshots.move_to_end(snapshot.scope)
            self._qualified_fallback.pop(snapshot.scope, None)
            if previous is not None:
                self.decisions.invalidate(snapshot.scope, cause="evidence_changed")
            while len(self._snapshots) > self._max_documents:
                old_scope, _ = self._snapshots.popitem(last=False)
                self._qualified_fallback.pop(old_scope, None)
                self.decisions.invalidate(old_scope, cause="evidence_changed")
                subscription = self._trace_subscriptions.pop(old_scope, None)
                if subscription is not None:
                    subscription.cancel()
            # Source notifications are separate from N2 invalidation events:
            # the first-ever source registration has no prior N2 decision.
            # Each visible list/detail handle owns one cancelable listener.
            listeners = tuple(self._source_subscriptions.get(snapshot.scope, {}).values())
        for handle, callback in listeners:
            if not handle.active:
                continue
            try:
                callback()  # GTK callback posts its own GLib idle.
            except Exception:
                # One disposed panel must not suppress other subscribers.
                pass

    def _subscribe_source(
        self, document: CoverDocumentScope, handle: CoverReadRequest,
        callback: Callable[[], None],
    ) -> None:
        """Attach only a bounded, cancelable observer to an exact N2 ref.

        Called while the service owns its lock in get_cover so an admission
        cannot slip between an initial MISS and subscription registration.
        """
        with self._lock:
            if self._source_subscription_count >= self._max_documents * 8:
                return
            bucket = self._source_subscriptions.get(document)
            if bucket is not None and len(bucket) >= 32:
                return
            if bucket is None:
                bucket = {}
                self._source_subscriptions[document] = bucket
            self._source_subscription_id += 1
            key = self._source_subscription_id
            bucket[key] = (handle, callback)
            self._source_subscription_count += 1
            handle._cancel_source_subscription = lambda: self._unsubscribe_source(document, key)

    def _unsubscribe_source(self, document: CoverDocumentScope, key: int) -> None:
        with self._lock:
            bucket = self._source_subscriptions.get(document)
            if bucket is None or bucket.pop(key, None) is None:
                return
            self._source_subscription_count -= 1
            if not bucket:
                self._source_subscriptions.pop(document, None)

    def cover_trace_snapshot(self, document: CoverDocumentScope) -> CoverTraceSnapshot | None:
        """Return the last observed steps for this registered document."""
        with self._lock:
            if document not in self._trace_subscriptions:
                return None
            return self._trace.snapshot(document)

    def observe_cover_for_audit(
        self, document: CoverDocumentScope, *, expected_revision: int | None = None,
    ) -> CoverAuditObservation:
        """Read one registered exact reference, not a historical N0 trace.

        No N1 resolution, provider request, cache population or EPUB scan.
        The caller must supply an already-authorized exact DocumentRef and
        perform this bounded physical check off the GTK main thread.
        """
        if type(document) is not CoverDocumentScope:
            raise TypeError("expected exact DocumentRef")
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 0
        ):
            raise ValueError("invalid expected source revision")
        with self._lock:
            snapshot = self._snapshots.get(document)
            if (self._closed or snapshot is None
                    or (expected_revision is not None
                        and snapshot.revision != expected_revision)):
                return CoverAuditObservation()
            seen = self.decisions.observe(document, revision=snapshot.revision)
        if seen is None:
            return CoverAuditObservation()
        _token, decision = seen
        physical = "unknown"
        if decision.status == "selected":
            media = self.loader.probe_media(decision.uri)
            if media.source_kind in ("embedded", "remote", "data"):
                if not media.source_cached:
                    physical = "not_validated"
                elif media.source_kind == "embedded":
                    physical = "digest_valid"
                else:
                    physical = "signature_valid"
        elif decision.status == "missing":
            physical = "not_applicable"
        # Never attribute bytes to a replaced or expired N2 generation.
        with self._lock:
            if (self._closed or self._snapshots.get(document) is not snapshot
                    or self.decisions.observe(
                        document, revision=snapshot.revision,
                    ) != seen):
                return CoverAuditObservation()
        return CoverAuditObservation(
            decision.status, decision.tier if decision.status == "selected" else "",
            physical,
        )

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
            # N3 is process-wide. Another profile may have restored the exact
            # content-addressed image after the failure was qualified; in
            # that case keep the original EPUB N1 preference, not the hot URL.
            # This check runs only on an explicit worker-side requalification,
            # never on the normal N2-hit book-list path.
            if valid_cached_cover_path(
                cover_cache_dir() / snapshot.embedded.resource.cache_name,
            ):
                return None

            fallback = self.decisions.resolve_n1(
                document, snapshot.candidates,
                revision=snapshot.revision, snapshot_complete=True,
                attested_book_identity=snapshot.attested_book_identity,
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

    def _qualify_failed_public_catalogue(
        self, document, snapshot, token, uri, handle, source_changed,
    ) -> None:
        """Bounded N2 fallback on an exact direct N3 HTTP absence receipt.

        Neither the cache's media HIT order nor a generic failure changes
        the documentary source. Keep the original canonical C immutable.
        """
        from ..core.notice_c_cover_index import is_canonical_cover_document

        if (not is_canonical_cover_document(document)
                or snapshot.attested_book_identity != document.source_key
                or not snapshot.snapshot_complete or snapshot.embedded is not None
                or len({item.source for item in snapshot.candidates}) < 2):
            return
        with self._lock:
            if (self._closed or not handle.active
                    or self._snapshots.get(document) is not snapshot
                    or self.decisions.selected_token(
                        document, revision=snapshot.revision, uri=uri,
                    ) != token):
                return
            remaining = self.loader.definitive_http_missing_ttl(uri)
            if remaining <= 0.0:
                return
            now = self._clock()
            prior = self._qualified_fallback.get(document)
            failed = ()
            until = now + min(remaining, self._fallback_ttl)
            if prior is not None:
                if (prior.token != token or prior.revision != snapshot.revision
                        or not prior.failed_catalogue_urls or prior.expires_at <= now):
                    return
                failed = prior.failed_catalogue_urls
                until = min(until, prior.expires_at)
            # At most the initial GET plus two qualified alternatives.
            # One URL shared by several providers is still one media failure.
            if uri in failed or len(failed) >= 2:
                return
            excluded = (*failed, uri)
            fallback = self.decisions.resolve_n1(
                document, tuple(item for item in snapshot.candidates if item.url not in excluded),
                revision=snapshot.revision, snapshot_complete=True,
                attested_book_identity=snapshot.attested_book_identity,
            )
            if (fallback.status != "selected" or fallback.tier != "catalog"
                    or fallback.uri in excluded):
                return
            next_token = self.decisions.begin(document, revision=snapshot.revision)
            if not self.decisions.publish_resolved(next_token, fallback):
                return
            self._qualified_fallback[document] = _QualifiedFallbackLease(
                snapshot.revision, fallback.uri, next_token, until, excluded,
            )
            # Include a reader refused by the bounded subscription registry,
            # and every other reader of the revoked token, even pending N3.
            subscribers = {handle: source_changed}
            for reader, callback in self._source_subscriptions.get(document, {}).values():
                if reader._selection_token == token:
                    subscribers[reader] = callback
        for reader, callback in subscribers.items():
            if reader.active:
                try:
                    callback()
                except Exception:
                    pass

    def _qualify_failed_embedded_in_worker(
        self,
        document: CoverDocumentScope,
        snapshot: CoverSourceSnapshot,
        token: CoverCacheToken,
        handle: CoverReadRequest,
        source_changed: Callable[[], None],
    ) -> None:
        """Single-flight bounded physical check, then exact N2 CAS + UI rebind.

        This is called ONLY after N3 returned None, never on an image-cache
        hit. The qualifier's expensive SHA/ZIP checks run on a worker. A
        stale/partial/ambiguous source cannot trigger a remote image fetch.
        The callback tells GTK to rebind the original DocumentRef, never to
        paint URL bytes under the old embedded token.
        """
        if (snapshot.embedded is None or snapshot.verified_epub_path is None
                or not snapshot.candidates or not handle.active):
            return
        key = (document, snapshot.revision)
        with self._lock:
            if (self._closed or self._snapshots.get(document) is not snapshot
                    or self._clock() < self._qualify_cooldown.get(key, 0)):
                return
            if self.decisions.selected_token(
                document, revision=snapshot.revision,
                uri=snapshot.embedded.candidate.url,
            ) != token:
                return
            if key in self._qualify_active:
                # List and detail must BOTH receive the new N2 token. A
                # single-flight physical verifier does not imply one viewer.
                waiters = self._qualify_waiters.setdefault(key, [])
                if len(waiters) < 32:
                    waiters.append((handle, source_changed))
                return
            # Bound queued SHA/ZIP jobs as well as live workers. A fast
            # scroll must not enqueue hundreds of full-file validations.
            if len(self._qualify_active) >= 16:
                return
            self._qualify_active.add(key)
            self._qualify_waiters[key] = [(handle, source_changed)]

        def work() -> None:
            changed = False
            subscribers = ()
            try:
                assessment = qualify_failed_epub_cover(
                    snapshot.embedded.resource,
                    source_path=snapshot.verified_epub_path,
                )
                with self._lock:
                    changed = self.apply_qualified_epub_fallback(
                        document, assessment, expected_token=token,
                    ) is not None
                    if changed:
                        # A second variant can still be waiting for its N3
                        # failure when the first has already revoked EPUB N2.
                        # Its late delivery is correctly rejected as stale;
                        # notify the exact old-token subscribers now instead.
                        subscribers = tuple(
                            (reader, callback)
                            for reader, callback in
                            self._source_subscriptions.get(document, {}).values()
                            if reader._selection_token == token
                        )
            except (OSError, TypeError, ValueError, RuntimeError):
                # No private path, source URL or ZIP exception can become a
                # GTK diagnostic. Treat any unexpected failure as retryable.
                pass
            finally:
                with self._lock:
                    waiters = self._qualify_waiters.pop(key, [])
                    self._qualify_active.discard(key)
                    if not changed and not self._closed:
                        self._qualify_cooldown[key] = self._clock() + 15.0
                        self._qualify_cooldown.move_to_end(key)
                        while len(self._qualify_cooldown) > self._max_documents:
                            self._qualify_cooldown.popitem(last=False)
            if changed:
                # A subscribed reader may also be in the qualifier's
                # waiters. One notification per handle, outside the lock.
                consumers = dict(subscribers)
                consumers.update(waiters)
                for waiting_handle, waiting_callback in consumers.items():
                    if not waiting_handle.active:
                        continue
                    try:
                        waiting_callback()
                    except Exception:
                        # A viewer may have closed while work was running.
                        # Never strand the other independent subscribers.
                        pass

        try:
            self._qualify_pool.submit(work)
        except RuntimeError:
            with self._lock:
                self._qualify_active.discard(key)
                self._qualify_waiters.pop(key, None)

    def _read_source_on_n2_miss(
        self,
        document: CoverDocumentScope,
        handle: CoverReadRequest,
        source_changed: Callable[[], None] | None,
    ) -> None:
        """Bounded, single-flight read of an admitted Core source snapshot.

        This may call a Core *cache reader* but never a provider, scan an EPUB,
        resolve books by title/ISBN or read a private account from the GUI.
        List/detail receive separate rebind notices for the same DocumentRef.
        The reader's None/error is unknown, NOT a 'missing cover' proof.
        """
        reader = self._cached_source_reader
        pool = self._read_pool
        if reader is None or pool is None:
            return
        # Legacy loan/local/Search refs cannot be found in the persisted
        # Notice C source index. A pure Core eligibility predicate avoids
        # scheduling unnecessary SQLite reads for recycled GTK cells.
        if self._cached_source_scope is not None:
            try:
                if self._cached_source_scope(document) is not True:
                    return
            except Exception:
                return  # Untrusted filter failure is unknown, not missing.
        with self._lock:
            if self._closed:
                return
            # A producer may register between get_cover's N2 MISS and
            # scheduling. Notify this visible consumer instead of stranding
            # its stale neutral placeholder.
            current_source_raced = document in self._snapshots
            if not current_source_raced:
                if self._clock() < self._read_cooldown.get(document, 0):
                    return
                if document in self._read_active:
                    waiters = self._read_waiters.setdefault(document, [])
                    if source_changed is not None and len(waiters) < 32:
                        waiters.append((handle, source_changed))
                    return
                # Includes queued reads, so rapidly recycled GTK cells
                # cannot enqueue unbounded Core source work.
                if len(self._read_active) >= 16:
                    return
                self._read_active.add(document)
                self._read_waiters[document] = (
                    [(handle, source_changed)] if source_changed is not None else []
                )

        if current_source_raced:
            # A registered subscription already saw that source transition.
            # Preserve the explicit callback for capacity-limited viewers
            # unable to acquire a subscription, without double GTK rebind.
            if (source_changed is not None and handle.active
                    and handle._cancel_source_subscription is None):
                try:
                    source_changed()
                except Exception:
                    pass
            return

        def work() -> None:
            # A positive producer hint can justify ONE extra read while the
            # original MISS is still in flight. Keep its entire waiter set:
            # the initiating GTK handle may have been recycled meanwhile.
            for attempt in range(2):
                try:
                    snapshot = reader(document)
                except Exception:
                    # A failed or revoked producer read has no negative meaning.
                    # In particular, never log provider credentials or URLs.
                    snapshot = None
                if (type(snapshot) is CoverSourceSnapshot
                        and snapshot.scope == document):
                    try:
                        self._register_snapshot(snapshot, only_if_absent=True)
                    except (ValueError, TypeError, RuntimeError):
                        pass
                with self._lock:
                    target = self._refresh_targets.get(document, 0)
                    current_snapshot = self._snapshots.get(document)
                    waiting = self._read_waiters.get(document, ())
                    subscribers = self._source_subscriptions.get(document, {})
                    if (attempt == 0 and target and not self._closed
                            and current_snapshot is None
                            and (handle.active
                                 or any(h.active for h, _ in waiting)
                                 or any(h.active for h, _ in subscribers.values()))):
                        # Keep the revision floor through the bounded retry;
                        # a new hint may raise it while the read is in flight.
                        self._read_cooldown.pop(document, None)
                        continue
                    live = not self._closed and current_snapshot is not None
                    waiters = self._read_waiters.pop(document, [])
                    self._read_active.discard(document)
                    if not live and not self._closed:
                        self._read_cooldown[document] = self._clock() + 15.0
                        self._read_cooldown.move_to_end(document)
                        while len(self._read_cooldown) > self._max_documents:
                            self._read_cooldown.popitem(last=False)
                    else:
                        self._read_cooldown.pop(document, None)
                    retry_newer = bool(
                        target and current_snapshot is not None
                        and current_snapshot.revision < target
                    )
                    if target and not retry_newer:
                        self._refresh_targets.pop(document, None)
                    break
            if retry_newer:
                self.refresh_cached_source_if_newer(document, target)
            if live:
                for waiting_handle, callback in waiters:
                    if (not waiting_handle.active
                            or waiting_handle._cancel_source_subscription is not None):
                        # register() already notified each live subscription;
                        # an extra notification would rebind list/detail twice.
                        continue
                    try:
                        callback()  # Unsubscribed/capacity-limited viewer.
                    except Exception:
                        pass

        try:
            pool.submit(work)
        except RuntimeError:
            with self._lock:
                self._read_active.discard(document)
                self._read_waiters.pop(document, None)

    def refresh_committed_source_in_worker(
        self, document: CoverDocumentScope, minimum_revision: int,
    ) -> bool:
        """Consume an exact Core post-commit epoch on its EXISTING worker.

        Only a warm document, a visible subscriber or an already in-flight
        first read warrants SQLite I/O. No new pool/submission/GET here.
        Re-read authoritative C, never construct N2 from a notification.
        Older/replayed hints are harmless; the normal snapshot CAS prevents
        a stale worker rolling back a concurrently installed newer value.
        """
        from ..core.notice_c_cover_index import is_canonical_cover_document

        if (not is_canonical_cover_document(document)
                or type(minimum_revision) is not int
                or not 1 <= minimum_revision < 2**63):
            return False
        reader = self._cached_source_reader
        if reader is None:
            return False
        if self._cached_source_scope is not None:
            try:
                if self._cached_source_scope(document) is not True:
                    return False
            except Exception:
                return False
        with self._lock:
            if self._closed:
                return False
            current = self._snapshots.get(document)
            if current is not None and current.revision >= minimum_revision:
                return False
            if (current is None and document not in self._read_active
                    and not self._source_subscriptions.get(document)):
                return False
        # Already off GTK, on the admitted Core worker and after COMMIT.
        # Do not acquire the service lock while reading SQLite.
        try:
            latest = reader(document)
            if (type(latest) is not CoverSourceSnapshot
                    or latest.scope != document
                    or latest.revision < minimum_revision):
                return False
            self._register_snapshot(latest, only_if_absent=False)
            return True
        except Exception:
            # A hot newer snapshot, service shutdown or failed read is not
            # documentary absence. Never invalidate unrelated N2/N3.
            return False

    def refresh_cached_source_if_newer(
        self, document: CoverDocumentScope, minimum_revision: int,
    ) -> bool:
        """Refresh a known N2 source only after a *new Core-persisted* epoch.

        The GUI may forward an ephemeral Core producer stamp, but cannot
        author the source value: the bounded worker reads Notice C again.
        A normal N2 HIT/recycled row never triggers SQLite I/O; a changed
        epoch invalidates only this exact DocumentRef, not shared N3 bytes.
        Concurrent revisions coalesce into one worker with a bounded retry.
        """
        if (type(document) is not CoverDocumentScope
                or type(minimum_revision) is not int
                or not 1 <= minimum_revision < 2**63):
            return False
        reader, pool = self._cached_source_reader, self._read_pool
        if reader is None or pool is None:
            return False
        if self._cached_source_scope is not None:
            try:
                if self._cached_source_scope(document) is not True:
                    return False
            except Exception:
                return False
        with self._lock:
            if self._closed:
                return False
            current = self._snapshots.get(document)
            if current is None:
                # A first N2 MISS may already be reading epoch v1 while the
                # Core writes v2. Preserve that newer producer hint until the
                # in-flight worker completes; do not enqueue a second read.
                if document in self._read_active:
                    self._refresh_targets[document] = max(
                        self._refresh_targets.get(document, 0), minimum_revision,
                    )
                return False
            if current.revision >= minimum_revision:
                return False  # Routine hot bind: no SQLite I/O.
            self._refresh_targets[document] = max(
                self._refresh_targets.get(document, 0), minimum_revision,
            )
            if document in self._refresh_active:
                return False
            if len(self._refresh_active) >= 16:
                self._refresh_targets.pop(document, None)
                return False
            self._refresh_active.add(document)
            self._stats["n2_source_refresh_scheduled"] += 1

        def work() -> None:
            retired = False
            try:
                for attempt in range(3):
                    with self._lock:
                        if self._closed:
                            return
                        target = self._refresh_targets.get(document, 0)
                    try:
                        latest = reader(document)
                    except Exception:
                        # Unknown is not a negative Notice C observation.
                        latest = None
                    if type(latest) is CoverSourceSnapshot and latest.scope == document:
                        try:
                            self._register_snapshot(
                                latest, only_if_absent=False,
                            )
                        except (ValueError, TypeError, RuntimeError):
                            # A newer concurrent snapshot can win; never
                            # roll back to a stale SQLite/source response.
                            pass
                    with self._lock:
                        current = self._snapshots.get(document)
                        requested = self._refresh_targets.get(document, 0)
                        if (self._closed or requested <= target
                                or current is not None
                                and current.revision >= requested
                                or attempt == 2):
                            # The decision to stop and retirement of this
                            # flight are ONE locked transition. A producer
                            # arriving just after unlock may own a new flight;
                            # this worker must never clear that newer target.
                            self._refresh_active.discard(document)
                            self._refresh_targets.pop(document, None)
                            retired = True
                            return
                    # A newer Core epoch arrived while the worker was
                    # reading the older snapshot. Retry only that exact
                    # source; never fetch a supplier or scan other books.
            finally:
                if not retired:
                    with self._lock:
                        self._refresh_active.discard(document)
                        self._refresh_targets.pop(document, None)

        try:
            pool.submit(work)
        except RuntimeError:
            with self._lock:
                self._refresh_active.discard(document)
                self._refresh_targets.pop(document, None)
            return False
        return True

    def get_cover(
        self,
        document: CoverDocumentScope, variant: CoverVariant,
        callback: Callable[[Path | None], None], *,
        width: int = 180, height: int = 270,
        on_source_changed: Callable[[], None] | None = None,
        minimum_revision: int | None = None,
    ) -> CoverReadRequest:
        """Use N2 HIT first; only N3 fetch/extraction follows an N1 selection.

        A Core-attested persisted C epoch is an optional read floor, not an
        N1 source decision. While the bounded cached-source worker refreshes
        an older snapshot, neither variant may materialize that stale N2.

        A missing physical EPUB image never makes the already-attested
        embedded source lose its N1 preference to a cached catalogue URL.
        A failed N3 recovery is reported as unavailable, not invented
        documentary 'missing'. For complete, attested public canonical C,
        a direct exact HTTP 404/410 may qualify a bounded alternative.
        """
        if type(document) is not CoverDocumentScope:
            raise TypeError("expected exact DocumentRef")
        document = CoverDecisionCache._snapshot_scope(document)
        if variant not in ("list", "detail"):
            raise ValueError("unknown cover variant")
        if not callable(callback):
            raise TypeError("expected cover callback")
        if on_source_changed is not None and not callable(on_source_changed):
            raise TypeError("expected source-change callback")
        if (type(width) is not int or type(height) is not int
                or not 1 <= width <= 2048 or not 1 <= height <= 2048):
            raise ValueError("invalid preview dimensions")
        if minimum_revision is not None:
            from ..core.notice_cover_repository import is_notice_cover_document
            if (type(minimum_revision) is not int
                    or not 1 <= minimum_revision < 2**63
                    or not is_notice_cover_document(document)):
                raise ValueError("invalid Notice C read revision")
        handle = CoverReadRequest()
        # Synchronous documentary decision only, including lock acquisition.
        # No N3 I/O, async read-through, callback or GTK paint time.
        started = perf_counter()
        with self._lock:
            if self._closed:
                raise RuntimeError("cover service is closed")
            self._live_reads.add(handle)
            if on_source_changed is not None:
                self._subscribe_source(document, handle, on_source_changed)
            snapshot = self._snapshots.get(document)
            below_floor = (
                snapshot is not None and minimum_revision is not None
                and snapshot.revision < minimum_revision
            )
            if snapshot is None:
                self._stats["n2_miss"] += 1
                self._stats["n2_source_unknown"] += 1
                outcome = "source_unknown"
                decision = None
            elif below_floor:
                # A previous consumer could have selected an older URI.
                # Do not resolve, reuse, or materialize that token on this
                # newer receipt. The producer still owns the source data.
                self._stats["n2_miss"] += 1
                outcome = "below_floor"
                decision = None
            else:
                self._snapshots.move_to_end(document)
                if document not in self._trace_subscriptions:
                    try:
                        self._trace_subscriptions[document] = self._trace.attach(
                            self.decisions, document,
                        )
                    except ValueError:
                        # Diagnostic capacity must never block cover reads.
                        pass
                lease = self._qualified_fallback.get(document)
                if lease is not None:
                    live = self.decisions.selected_token(
                        document, revision=lease.revision, uri=lease.url,
                    )
                    if (snapshot.revision != lease.revision
                            or live != lease.token):
                        self._qualified_fallback.pop(document, None)
                    elif self._clock() >= lease.expires_at:
                        # Reconsider the original N1 priority after a bounded
                        # lease. Repair of N3 can restore the image; no
                        # temporary alternate may permanently displace it.
                        self._qualified_fallback.pop(document, None)
                        self.decisions.invalidate(
                            document, cause="materialization_requalified",
                        )
                decision = self.decisions.peek(document, revision=snapshot.revision)
                if decision is None:
                    self._stats["n2_miss"] += 1
                    self._stats["n2_resolution_miss"] += 1
                    outcome = "resolution_miss"
                else:
                    self._stats["n2_hit"] += 1
                    outcome = "hit"
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
                            attested_book_identity=snapshot.attested_book_identity,
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
            # Bind the token while registration and fallback CAS are
            # serialized. Even a reader not yet dispatched to N3 belongs
            # to the exact selection it must later be notified to rebind.
            handle._selection_token = selected_token
            elapsed_ms = (perf_counter() - started) * 1000
            if math.isfinite(elapsed_ms) and elapsed_ms >= 0:
                self._n2_recent[outcome].append(elapsed_ms)
        if snapshot is None or decision is None or selected_token is None:
            # A pending or missing read can have queued a GTK idle before
            # Core registers a stronger N2 source. Never let its delayed
            # neutral placeholder hide the newly selected exact cover.
            def still_unselected() -> bool:
                with self._lock:
                    return (
                        not self._closed
                        and self._snapshots.get(document) is snapshot
                        and (
                            snapshot is None
                            or below_floor
                            or self.decisions.peek(
                                document, revision=snapshot.revision,
                            ) is decision
                        )
                    )

            handle._is_selected = still_unselected
            if snapshot is None:
                self._read_source_on_n2_miss(
                    document, handle, on_source_changed,
                )
            if minimum_revision is not None and (snapshot is None or below_floor):
                # For a cold read this also preserves an epoch arriving
                # behind an already-active MISS. The common worker remains
                # responsible for reading and publishing exact evidence.
                self.refresh_cached_source_if_newer(document, minimum_revision)
            callback(None)
            return handle
        # The return handle exposes the N2 source before any synchronous
        # N3 cache-hit callback can be painted on GTK's idle queue.
        handle._source_uri = decision.uri

        def selected_now() -> bool:
            with self._lock:
                return (
                    not self._closed
                    and self._snapshots.get(document) is snapshot
                    and self.decisions.selected_token(
                        document, revision=snapshot.revision, uri=decision.uri,
                    ) == selected_token
                )

        handle._is_selected = selected_now

        def record_texture() -> bool:
            with self._lock:
                return handle.still_current and self._trace.note_painted(
                    selected_token, surface=variant,
                )

        def delivered(path: Path | None) -> None:
            with self._lock:
                if not handle.still_current:
                    return
                if path is not None and document in self._trace_subscriptions:
                    # N3 has validated the source for both variants. A list
                    # read may return the source itself if previewing fails.
                    preview_seen = (
                        True if variant == "list" and path.parent == self.loader.preview_root
                        else None
                    )
                    recorded = self._trace.note_media(
                        selected_token, source_cached=True, preview_cached=preview_seen,
                    )
                    if recorded:
                        handle._offer_paint_receipt(path, record_texture)
            if handle.still_current:
                callback(path)
                if (path is None and decision.tier == "embedded"
                        and on_source_changed is not None):
                    self._qualify_failed_embedded_in_worker(
                        document, snapshot, selected_token, handle,
                        on_source_changed,
                    )
                elif (path is None and decision.tier == "catalog"
                      and on_source_changed is not None):
                    self._qualify_failed_public_catalogue(
                        document, snapshot, selected_token, decision.uri,
                        handle, on_source_changed,
                    )

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

    def stats(self) -> dict[str, int | float]:
        """Separate N1 resolution and N2 synchronous decision statistics."""
        with self._lock:
            stats = {
                **self._stats, **self.decisions.n1_latency_stats(),
                "n2_registered_documents": len(self._snapshots),
                "n2_source_flights": len(self._read_active),
            }
            for outcome, window in self._n2_recent.items():
                prefix = f"n2_{outcome}"
                recent = sorted(window)
                stats[f"{prefix}_recent_count"] = count = len(recent)
                if count:
                    stats[f"{prefix}_recent_p50_ms"] = round(recent[(count * 50 + 99) // 100 - 1], 2)
                    stats[f"{prefix}_recent_p95_ms"] = round(recent[(count * 95 + 99) // 100 - 1], 2)
            return stats

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # The widget's CoverReadRequest must be canceled as well as its
            # service-side observer removed. Otherwise a worker that captured
            # callbacks before close can post a late GTK rebind to a disposed
            # window, even though _closed revoked the N2 selection.
            handles = set(self._live_reads)
            self._live_reads.clear()
            handles.update(handle for bucket in self._source_subscriptions.values()
                           for handle, _callback in bucket.values())
            handles.update(
                handle for waiters in self._read_waiters.values()
                for handle, _callback in waiters
            )
            handles.update(
                handle for waiters in self._qualify_waiters.values()
                for handle, _callback in waiters
            )
            self._snapshots.clear()
            self._source_subscriptions.clear()
            self._source_subscription_count = 0
            for subscription in self._trace_subscriptions.values():
                subscription.cancel()
            self._trace_subscriptions.clear()
            self._resolving.clear()
            self._read_active.clear()
            self._refresh_active.clear()
            self._refresh_targets.clear()
            self._read_waiters.clear()
            self._read_cooldown.clear()
            self._qualified_fallback.clear()
            self._qualify_active.clear()
            self._qualify_waiters.clear()
            self._qualify_cooldown.clear()
        # Cancellation may touch N3 or unsubscribe from this service. Do not
        # hold the service lock while calling foreign request implementations.
        for handle in handles:
            handle.cancel()
        self._qualify_pool.shutdown(wait=False, cancel_futures=True)
        if self._read_pool is not None:
            self._read_pool.shutdown(wait=False, cancel_futures=True)


__all__ = [
    "CoverSourceSnapshot", "CoverReadRequest", "DocumentCoverService",
]
