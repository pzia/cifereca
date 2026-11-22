"""Exact-scope N2 cover-decision cache and invalidation notifications (#6168).

N1 owns admissibility and provenance; N2 caches ONLY N1 decisions. N3
CoverLoader owns bytes and resized previews; no URL fetch, EPUB scan or
filesystem I/O happens here. Producers must call invalidate/invalidate_many
when their catalogue, account epoch, Artifact, Placement, OriginLink, loan
or evidence changes. A cache hit alone is NOT evidence of identity linkage.

Subscriptions are synchronous, thread-agnostic Core notifications: the GTK
adapter MUST dispatch its presentation update on the GTK main context and
check SourceKey / generation when handling the event.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import logging
import math
from threading import Lock
import time
from time import perf_counter
from typing import Literal

from .book_cover_projection import (
    CoverDocumentScope, ResolvedBookCover, resolve_book_cover,
)
from .covers import CoverCandidate, candidate

CoverChangeKind = Literal["refresh", "resolved", "invalidated"]
CoverInvalidationCause = Literal[
    "catalogue_changed", "account_epoch_changed", "identity_changed",
    "artifact_changed", "placement_changed", "origin_link_changed",
    "loan_changed", "entitlement_changed", "evidence_changed",
    "materialization_requalified",
]
_LOG = logging.getLogger("cifereca")
_N1_LATENCY_WINDOW = 1024


@dataclass(frozen=True, slots=True)
class CoverCacheToken:
    scope: CoverDocumentScope
    revision: int
    generation: int


@dataclass(frozen=True, slots=True)
class CoverDecisionChange:
    scope: CoverDocumentScope
    revision: int
    generation: int
    kind: CoverChangeKind
    decision: ResolvedBookCover | None = None
    cause: CoverInvalidationCause | None = None
    sequence: int = 0


@dataclass(slots=True)
class _Slot:
    token: CoverCacheToken
    decision: ResolvedBookCover | None = None
    expires_at: float = 0.0
    published: bool = False


class CoverSubscription:
    """Cancellation is idempotent; no callbacks are executed while locked."""

    def __init__(self, owner: CoverDecisionCache, scope: CoverDocumentScope, key: int):
        self._owner = owner
        self._scope = scope
        self._key = key

    def cancel(self) -> None:
        owner = self._owner
        if owner is None:
            return
        self._owner = None
        owner._unsubscribe(self._scope, self._key)


class CoverDecisionCache:
    """Bounded in-memory decisions; exact-scope CAS rejects stale completions.

    A caller starts one source snapshot with begin(...). It publishes ONLY
    with that token. A newer begin or invalidation revokes the old token, even
    when a late callback has a visually identical URI. Revision and generation
    are not inferred from UI navigation; they are supplied by the producer.
    """

    def __init__(
        self, *, max_scopes: int = 2048, max_subscribers: int = 4096,
        selected_ttl: float = 900.0, missing_ttl: float = 30.0,
        ambiguous_ttl: float = 15.0, pending_ttl: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if (type(max_scopes) is not int or not 1 <= max_scopes <= 20000
                or type(max_subscribers) is not int
                or not 1 <= max_subscribers <= 50000):
            raise ValueError("invalid cover cache capacity")
        for ttl in (selected_ttl, missing_ttl, ambiguous_ttl, pending_ttl):
            if (type(ttl) not in (int, float) or not 0 < ttl <= 86400
                    or not float("-inf") < ttl < float("inf")):
                raise ValueError("invalid cover decision TTL")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._max_scopes = max_scopes
        self._max_subscribers = max_subscribers
        self._ttl = {
            "selected": float(selected_ttl),
            "missing": float(missing_ttl),
            "ambiguous": float(ambiguous_ttl),
            "pending": float(pending_ttl),
        }
        self._clock = clock
        self._lock = Lock()
        self._slots: OrderedDict[CoverDocumentScope, _Slot] = OrderedDict()
        self._subscribers: dict[
            CoverDocumentScope, dict[int, Callable[[CoverDecisionChange], None]]
        ] = {}
        self._generation = 0
        self._event_sequence = 0
        self._listener_id = 0
        self._subscriber_count = 0
        self._n1_recent = {
            status: deque(maxlen=_N1_LATENCY_WINDOW)
            for status in ("selected", "missing", "pending", "ambiguous")
        }
        self._n1_counts = dict.fromkeys(self._n1_recent, 0)

    def _bump(self) -> int:
        self._generation += 1
        return self._generation

    def _next_event(self) -> int:
        self._event_sequence += 1
        return self._event_sequence

    def _record(self, scope: CoverDocumentScope, slot: _Slot) -> None:
        self._slots[scope] = slot
        self._slots.move_to_end(scope)
        while len(self._slots) > self._max_scopes:
            self._slots.popitem(last=False)

    def _listeners(
        self, scope: CoverDocumentScope,
    ) -> tuple[Callable[[CoverDecisionChange], None], ...]:
        return tuple(self._subscribers.get(scope, {}).values())

    @staticmethod
    def _notify(
        callbacks: tuple[Callable[[CoverDecisionChange], None], ...],
        change: CoverDecisionChange,
    ) -> None:
        for callback in callbacks:
            try:
                callback(change)
            except Exception:
                # A failing detail listener cannot block a list listener.
                _LOG.exception("cover-decision subscriber failed")

    @staticmethod
    def _require_scope(scope: CoverDocumentScope) -> None:
        """Check a frozen-but-mutable source before dictionary hashing/equality.

        An exact dataclass type is insufficient: callers may have replaced
        fields via object.__setattr__ after the original construction.
        """
        if type(scope) is not CoverDocumentScope:
            raise TypeError("expected exact cover scope")
        # The constructor in older admitted scopes does not first type-check
        # the kind; avoid invoking hostile equality from a mutated object.
        if type(scope.kind) is not str:
            raise ValueError("invalid cover scope kind")
        scope.__post_init__()

    def begin(self, scope: CoverDocumentScope, *, revision: int) -> CoverCacheToken:
        """Start a complete or partial observation, revoking older work."""
        self._require_scope(scope)
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid cover revision")
        with self._lock:
            previous = self._slots.get(scope)
            if previous is not None and revision < previous.token.revision:
                raise ValueError("stale cover revision")
            token = CoverCacheToken(scope, revision, self._bump())
            self._record(scope, _Slot(token))
            callbacks = self._listeners(scope)
            event = CoverDecisionChange(
                scope, revision, token.generation, "refresh",
                sequence=self._next_event(),
            )
        self._notify(callbacks, event)
        return token

    def peek(
        self, scope: CoverDocumentScope, *, revision: int,
    ) -> ResolvedBookCover | None:
        """Exact scoped/revisioned hit; expiry never becomes a \u2018missing\u2019 proof."""
        self._require_scope(scope)
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid cover revision")
        with self._lock:
            slot = self._slots.get(scope)
            if slot is None or slot.token.revision != revision:
                return None
            if slot.decision is None or self._clock() >= slot.expires_at:
                slot.decision = None
                return None
            self._slots.move_to_end(scope)
            return slot.decision

    def observe(
        self, scope: CoverDocumentScope, *, revision: int,
    ) -> tuple[CoverCacheToken, ResolvedBookCover] | None:
        """Observe an existing N2 decision without touching TTL, LRU or slots.

        Exact token + decision let a reader reject changes during external
        physical observation. Unknown/expired is never documentary missing.
        """
        if type(scope) is not CoverDocumentScope:
            raise TypeError("expected exact cover scope")
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid cover revision")
        with self._lock:
            slot = self._slots.get(scope)
            if (slot is None or slot.token.revision != revision
                    or not slot.published or slot.decision is None
                    or self._clock() >= slot.expires_at):
                return None
            return slot.token, slot.decision

    def selected_token(
        self, scope: CoverDocumentScope, *,
        revision: int, uri: str,
    ) -> CoverCacheToken | None:
        """Lease for an exact, currently selected N1 image (N0/N4 #6168).

        A GTK texture-applied observer must not reconstruct a generation
        from the Loan ID, image URI, catalogue ISBN or an expired cache hit.
        It may only report a physical decode/paint after matching this
        source scope, producer revision AND the selected URI still present in
        N2. Returned tokens are immutable; N0 subsequently rejects them if
        an invalidation or newer observation overtakes the GTK callback.

        Read-only: no publisher callback, URL fetch, preview scan or rights
        inference. Missing, pending and ambiguous decisions cannot paint.
        """
        if type(scope) is not CoverDocumentScope:
            raise TypeError("expected exact cover scope")
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid cover revision")
        if type(uri) is not str or not uri:
            return None
        with self._lock:
            current = self._slots.get(scope)
            if (current is None or current.token.revision != revision
                    or current.decision is None or not current.published):
                return None
            if self._clock() >= current.expires_at:
                current.decision = None
                return None
            decision = current.decision
            if decision.status != "selected" or decision.uri != uri:
                return None
            self._slots.move_to_end(scope)
            return current.token

    @staticmethod
    def _validate_token(token: CoverCacheToken) -> None:
        if type(token) is not CoverCacheToken:
            raise TypeError("expected cover cache token")
        # Dataclass equality otherwise aliases True/1 and 1.0/1.
        # Validate before slot lookup or N1 resolution, not just on begin().
        if (type(token.revision) is not int or token.revision < 0
                or type(token.generation) is not int or token.generation < 1):
            raise ValueError("invalid cover cache token")
        CoverDecisionCache._require_scope(token.scope)

    def resolve_and_publish(
        self, token: CoverCacheToken, candidates: Iterable[CoverCandidate], *,
        snapshot_complete: bool,
        attested_book_identity: str = "",
        catalog_priorities: Mapping[str, int] | None = None,
    ) -> bool:
        """Return False for a late, invalidated or evicted source snapshot.

        N1 validates all candidate URLs and provenance before we cache them.
        This method does no I/O and never turns a partial result into missing.
        """
        self._validate_token(token)
        with self._lock:
            current = self._slots.get(token.scope)
            if current is None or current.token != token or current.published:
                return False
        decision = self.resolve_n1(
            token.scope, candidates, revision=token.revision,
            snapshot_complete=snapshot_complete,
            attested_book_identity=attested_book_identity,
            catalog_priorities=catalog_priorities,
        )
        return self.publish_resolved(token, decision)

    def resolve_n1(
        self, scope: CoverDocumentScope, candidates: Iterable[CoverCandidate], *,
        revision: int, snapshot_complete: bool,
        attested_book_identity: str = "",
        catalog_priorities: Mapping[str, int] | None = None,
    ) -> ResolvedBookCover:
        """Measure the existing pure resolver, not its N2 CAS or callbacks.

        Counts actual resolver results (even if publication later loses its
        token). Warm N2 hits, rejected tokens and direct publication do not
        perform resolution. The bounded windows contain no source identifiers.
        """
        started = perf_counter()
        decision = resolve_book_cover(
            scope, candidates, revision=revision,
            snapshot_complete=snapshot_complete,
            attested_book_identity=attested_book_identity,
            catalog_priorities=catalog_priorities,
        )
        elapsed_ms = (perf_counter() - started) * 1000
        with self._lock:
            self._n1_counts[decision.status] += 1
            if math.isfinite(elapsed_ms) and elapsed_ms >= 0:
                self._n1_recent[decision.status].append(elapsed_ms)
        return decision

    def n1_latency_stats(self) -> dict[str, int | float]:
        """Read only, bounded nearest-rank percentiles by resolver outcome."""
        with self._lock:
            stats = {}
            for status, samples in self._n1_recent.items():
                prefix = f"n1_{status}"
                recent = sorted(samples)
                stats[f"{prefix}_count"] = self._n1_counts[status]
                stats[f"{prefix}_recent_count"] = count = len(recent)
                if count:
                    stats[f"{prefix}_recent_p50_ms"] = round(recent[(count * 50 + 99) // 100 - 1], 2)
                    stats[f"{prefix}_recent_p95_ms"] = round(recent[(count * 95 + 99) // 100 - 1], 2)
            return stats

    @staticmethod
    def _validate_resolved_decision(decision: ResolvedBookCover) -> None:
        """Reject malformed or cross-status decisions at the N1-to-N2 boundary.

        Only a Core-produced exact-source N1 decision is eligible. Structural
        validation is an additional guard against accidental UI/DTO replay;
        it does not turn a supplied source coordinate into an attestation.
        """
        if (type(decision) is not ResolvedBookCover
                or decision.schema != "cifereca.resolved-book-cover/1"
                or type(decision.revision) is not int or decision.revision < 0
                or type(decision.candidate_count) is not int
                or not 0 <= decision.candidate_count <= 4096):
            raise ValueError("invalid cover decision contract")
        CoverDecisionCache._require_scope(decision.scope)
        status_reasons = {
            "selected": ("attested",),
            "missing": ("complete_no_admissible_cover",),
            "pending": ("partial_snapshot", "source_failed", "source_overflow"),
            "ambiguous": ("conflicting_evidence",),
        }
        if (type(decision.status) is not str
                or decision.reason not in status_reasons.get(decision.status, ())):
            raise ValueError("invalid cover decision status")
        if decision.status == "selected":
            if (decision.candidate_count < 1
                    or type(decision.tier) is not str
                    or type(decision.uri) is not str
                    or type(decision.source) is not str
                    or type(decision.evidence_id) is not str):
                raise ValueError("invalid selected cover evidence")
            checked = candidate(
                decision.uri, tier=decision.tier,
                source=decision.source, evidence_id=decision.evidence_id,
            )
            if checked is None or checked.url != decision.uri:
                raise ValueError("invalid selected cover URI/provenance")
        elif (decision.uri != "" or decision.source != ""
              or decision.evidence_id != ""):
            raise ValueError("non-selected cover must not have a selected URI")
        elif decision.status == "ambiguous":
            if (decision.tier not in ("embedded", "origin", "catalog")
                    or decision.candidate_count < 2):
                raise ValueError("invalid ambiguous cover evidence")
        elif decision.tier != "" or decision.candidate_count != 0:
            raise ValueError("invalid empty cover decision")

    def publish_resolved(
        self, token: CoverCacheToken, decision: ResolvedBookCover,
    ) -> bool:
        """Publish an already-resolved N1 snapshot once to all consumers.

        The producing Core adapter determines the exact scope and source
        revision. A mismatched snapshot raises before any cache mutation; a
        stale or invalidated token returns False without notifying listeners.
        Does not re-run resolution, read EPUBs, fetch URLs or touch GTK.
        """
        self._validate_token(token)
        self._validate_resolved_decision(decision)
        if decision.scope != token.scope or decision.revision != token.revision:
            raise ValueError("cover decision scope or revision mismatch")
        with self._lock:
            current = self._slots.get(token.scope)
            if current is None or current.token != token or current.published:
                return False
            current.published = True
            current.decision = decision
            current.expires_at = self._clock() + self._ttl[decision.status]
            self._slots.move_to_end(token.scope)
            callbacks = self._listeners(token.scope)
            event = CoverDecisionChange(
                token.scope, token.revision, token.generation,
                "resolved", decision=decision,
                sequence=self._next_event(),
            )
        self._notify(callbacks, event)
        return True

    def invalidate(
        self, scope: CoverDocumentScope, *,
        cause: CoverInvalidationCause,
    ) -> None:
        self._require_scope(scope)
        if cause not in (
            "catalogue_changed", "account_epoch_changed", "identity_changed",
            "artifact_changed", "placement_changed", "origin_link_changed",
            "loan_changed", "entitlement_changed", "evidence_changed",
            "materialization_requalified",
        ):
            raise ValueError("unknown cover invalidation cause")
        with self._lock:
            current = self._slots.get(scope)
            revision = current.token.revision if current else 0
            generation = self._bump()
            # Preserve last known source revision; later workers must advance
            # to a newer producer revision on true source changes.
            self._record(scope, _Slot(CoverCacheToken(scope, revision, generation)))
            callbacks = self._listeners(scope)
            event = CoverDecisionChange(
                scope, revision, generation, "invalidated", cause=cause,
                sequence=self._next_event(),
            )
        self._notify(callbacks, event)

    def invalidate_many(
        self, scopes: Iterable[CoverDocumentScope], *,
        cause: CoverInvalidationCause,
    ) -> int:
        """Validate and bound the entire batch before changing any scope."""
        unique: dict[CoverDocumentScope, None] = {}
        for index, scope in enumerate(scopes):
            if index >= self._max_scopes:
                raise ValueError("cover invalidation batch too large")
            self._require_scope(scope)
            unique[scope] = None
        # Preserve the exact-event validation behavior even for empty batches.
        if not unique:
            if cause not in (
                "catalogue_changed", "account_epoch_changed", "identity_changed",
                "artifact_changed", "placement_changed", "origin_link_changed",
                "loan_changed", "entitlement_changed", "evidence_changed",
                "materialization_requalified",
            ):
                raise ValueError("unknown cover invalidation cause")
            return 0
        for scope in unique:
            self.invalidate(scope, cause=cause)
        return len(unique)

    def subscribe(
        self, scope: CoverDocumentScope,
        callback: Callable[[CoverDecisionChange], None],
    ) -> CoverSubscription:
        if not callable(callback):
            raise TypeError("invalid cover subscriber")
        self._require_scope(scope)
        with self._lock:
            if self._subscriber_count >= self._max_subscribers:
                raise ValueError("too many cover decision subscribers")
            self._listener_id += 1
            key = self._listener_id
            self._subscribers.setdefault(scope, {})[key] = callback
            self._subscriber_count += 1
        return CoverSubscription(self, scope, key)

    def _unsubscribe(self, scope: CoverDocumentScope, key: int) -> None:
        with self._lock:
            listeners = self._subscribers.get(scope)
            if listeners is None or key not in listeners:
                return
            listeners.pop(key)
            self._subscriber_count -= 1
            if not listeners:
                del self._subscribers[scope]

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "scopes": len(self._slots),
                "subscribers": self._subscriber_count,
            }


__all__ = [
    "CoverCacheToken", "CoverDecisionChange", "CoverSubscription",
    "CoverDecisionCache",
]
