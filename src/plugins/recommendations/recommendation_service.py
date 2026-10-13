from __future__ import annotations

from dataclasses import dataclass
import heapq
import logging
from threading import Event, Lock
import time
from typing import Any, Callable

from recommendation_engine import (
    CatalogNoticeVariant,
    RankedCandidate,
    _remember_notice,
    build_profile,
    build_queries,
    candidate_key,
    get_field,
    is_already_present,
    normalize,
)
from recommendation_engine_fast import rank_candidates_prepared, score_candidate_cached


_LOG = logging.getLogger("cifereca.plugin.recommendations")


@dataclass(frozen=True)
class LibrarySnapshotLoad:
    books: tuple[Any, ...]
    elapsed_ms: float
    cached: bool


class LibrarySnapshotCache:
    """Session cache for persisted library projections, keyed by identity."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._generation = 0
        self._identity_generations: dict[str, int] = {}
        self._books: dict[str, tuple[Any, ...]] = {}
        self._inflight: dict[tuple[str, int, int], Event] = {}

    def invalidate(self, identity_id: str | None = None) -> None:
        with self._lock:
            if identity_id is None:
                self._generation += 1
                self._books.clear()
                return
            identity_key = str(identity_id or "")
            self._identity_generations[identity_key] = (
                self._identity_generations.get(identity_key, 0) + 1
            )
            self._books.pop(identity_key, None)

    def load(self, metadata_broker: Any, identity_id: str) -> LibrarySnapshotLoad:
        identity_key = str(identity_id or "")
        while True:
            with self._lock:
                if identity_key in self._books:
                    books = self._books[identity_key]
                    _LOG.info(
                        "TRACE recommendations library-snapshot books=%s snapshot_ms=%.3f cached=true",
                        len(books),
                        0.0,
                    )
                    return LibrarySnapshotLoad(books, 0.0, True)

                generation = self._generation
                identity_generation = self._identity_generations.get(identity_key, 0)
                inflight_key = (identity_key, generation, identity_generation)
                waiter = self._inflight.get(inflight_key)
                leader = waiter is None
                if leader:
                    waiter = Event()
                    self._inflight[inflight_key] = waiter

            if not leader:
                waiter.wait()
                continue

            started = time.perf_counter()
            try:
                books = tuple(
                    metadata_broker.library_entries(
                        identity_id=identity_id,
                        scan=False,
                    )
                    or ()
                )
            except Exception:
                with self._lock:
                    current = self._inflight.get(inflight_key)
                    if current is waiter:
                        self._inflight.pop(inflight_key, None)
                        waiter.set()
                raise
            elapsed_ms = (time.perf_counter() - started) * 1000.0

            with self._lock:
                fresh = (
                    generation == self._generation
                    and identity_generation
                    == self._identity_generations.get(identity_key, 0)
                )
                if fresh:
                    self._books[identity_key] = books
                current = self._inflight.get(inflight_key)
                if current is waiter:
                    self._inflight.pop(inflight_key, None)
                    waiter.set()

            if not fresh:
                _LOG.info(
                    "TRACE recommendations library-snapshot books=%s snapshot_ms=%.3f cached=false stale=true",
                    len(books),
                    elapsed_ms,
                )
                continue

            _LOG.info(
                "TRACE recommendations library-snapshot books=%s snapshot_ms=%.3f cached=false",
                len(books),
                elapsed_ms,
            )
            return LibrarySnapshotLoad(books, elapsed_ms, False)


@dataclass(frozen=True)
class RecommendationResult:
    books_count: int
    queries: tuple[str, ...]
    ranked: tuple[RankedCandidate, ...]
    providers: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...] = ()
    cache_batches: int = 0
    network_batches: int = 0
    cache_unknown_calls: int = 0
    cancelled: bool = False
    profile_books: tuple[Any, ...] = ()


@dataclass(frozen=True)
class RecommendationPartial:
    ranked: tuple[RankedCandidate, ...]
    query_index: int
    query_total: int
    provider_done: int
    provider_total: int
    results_seen: int
    cache_batches: int
    network_batches: int
    providers: tuple[tuple[str, str], ...] = ()


def _merge_provider_rows(grouped: dict[str, list[Any]], providers: tuple[tuple[str, str], ...]) -> list[Any]:
    order = [str(pid) for pid, _label in providers if str(pid) in grouped]
    order.extend(sorted(pid for pid in grouped if pid not in order))
    maximum = max((len(grouped[pid]) for pid in order), default=0)
    return [grouped[pid][rank] for rank in range(maximum) for pid in order if rank < len(grouped[pid])]


def _cancelled(control: Any) -> bool:
    if control is None:
        return False
    try:
        fn = getattr(control, "cancelled", None)
        return bool(fn()) if callable(fn) else False
    except Exception:
        return True


def _provisional_head(
    provisional: dict[
        str, tuple[Any, float, tuple[str, ...], list[str], dict[tuple[str, ...], CatalogNoticeVariant]]
    ],
    limit: int,
) -> list[RankedCandidate]:
    """Return only the useful provisional head without sorting the full candidate set."""
    maximum = max(1, int(limit))
    rows = (
        RankedCandidate(
            item=item,
            score=max(1, min(100, int(round(score)))),
            reasons=reasons,
            sources=tuple(sources),
            query_hits=0,
            notices=tuple(notices.values()),
        )
        for item, score, reasons, sources, notices in provisional.values()
        if score > 0.0
    )
    return heapq.nsmallest(
        maximum,
        rows,
        key=lambda row: (
            -row.score,
            normalize(get_field(row.item, "title", "")),
            normalize(get_field(row.item, "author", "")),
        ),
    )


def compute_recommendations(
    metadata_broker: Any,
    catalog_broker: Any,
    identity_id: str,
    *,
    scope: str = "digital",
    max_queries: int = 8,
    per_query: int = 50,
    max_results: int = 120,
    exploration: int = 25,
    exclude_owned: bool = True,
    snapshot_cache: LibrarySnapshotCache | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    partial: Callable[[RecommendationPartial], None] | None = None,
    control: Any = None,
) -> RecommendationResult:
    total_started = time.perf_counter()

    # Persisted projection only: never trigger a filesystem scan here.
    if snapshot_cache is None:
        snapshot_started = time.perf_counter()
        books = list(
            metadata_broker.library_entries(identity_id=identity_id, scan=False) or []
        )
        snapshot_ms = (time.perf_counter() - snapshot_started) * 1000.0
        snapshot_cached = False
    else:
        snapshot = snapshot_cache.load(metadata_broker, identity_id)
        books = list(snapshot.books)
        snapshot_ms = snapshot.elapsed_ms
        snapshot_cached = snapshot.cached

    profile_started = time.perf_counter()
    profile = build_profile(books)
    queries = build_queries(books, max_queries=max_queries, profile=profile)
    profile_ms = (time.perf_counter() - profile_started) * 1000.0

    providers_started = time.perf_counter()
    providers = tuple(catalog_broker.searchable_providers() or ())
    providers_ms = (time.perf_counter() - providers_started) * 1000.0

    def trace_performance(*, catalog_ms: float = 0.0, rank_ms: float = 0.0, cancelled: bool = False) -> None:
        _LOG.info(
            "TRACE recommendations performance books=%s queries=%s providers=%s "
            "snapshot_ms=%.3f snapshot_cached=%s profile_ms=%.3f providers_ms=%.3f "
            "catalog_ms=%.3f rank_ms=%.3f total_ms=%.3f cancelled=%s",
            len(books),
            len(queries),
            len(providers),
            snapshot_ms,
            str(bool(snapshot_cached)).lower(),
            profile_ms,
            providers_ms,
            catalog_ms,
            rank_ms,
            (time.perf_counter() - total_started) * 1000.0,
            str(bool(cancelled)).lower(),
        )

    if not books or not queries or not providers:
        trace_performance()
        return RecommendationResult(
            len(books), queries, (), providers, profile_books=tuple(books)
        )

    batches: list[list[Any]] = []
    warnings: list[str] = []
    cache_batches = network_batches = cache_unknown_calls = results_seen = 0
    provider_total = max(1, len(providers))
    total_units = max(1, len(queries) * provider_total)
    progressive = getattr(catalog_broker, "search_progressive", None)
    was_cancelled = False

    if progress:
        progress(
            0,
            total_units,
            f"Profil prêt · {len(books)} livres · {len(queries)} signaux · catalogues 0/{provider_total}",
        )

    # Provisional ranking is intentionally lightweight: content score once per
    # candidate, no RRF/MMR. Full ranking runs once after all catalog queries.
    provisional: dict[
        str, tuple[Any, float, tuple[str, ...], list[str], dict[tuple[str, ...], CatalogNoticeVariant]]
    ] = {}
    score_cache: dict[tuple[Any, ...], tuple[float, tuple[str, ...]]] = {}

    def ingest_provisional(rows: list[Any]) -> None:
        for item in rows:
            key = candidate_key(item)
            if exclude_owned and is_already_present(profile, item):
                continue
            score, reasons = score_candidate_cached(
                profile, item, score_cache, known_absent=True
            )
            source = str(get_field(item, "source_provider", "") or "")
            previous = provisional.get(key)
            if previous is None:
                notices: dict[tuple[str, ...], CatalogNoticeVariant] = {}
                _remember_notice(notices, item)
                provisional[key] = (item, score, reasons, [source] if source else [], notices)
                continue
            prev_item, prev_score, prev_reasons, sources, notices = previous
            _remember_notice(notices, item)
            if source and source not in sources:
                sources.append(source)
            provisional[key] = (
                (item, score, reasons, sources, notices)
                if score > prev_score
                else (prev_item, prev_score, prev_reasons, sources, notices)
            )

    def emit_partial(query_index: int, provider_done: int) -> None:
        if partial is None or _cancelled(control):
            return
        ranked = _provisional_head(
            provisional,
            min(40, max(1, int(max_results))),
        )
        partial(
            RecommendationPartial(
                ranked=tuple(ranked),
                query_index=query_index,
                query_total=len(queries),
                provider_done=provider_done,
                provider_total=provider_total,
                results_seen=results_seen,
                cache_batches=cache_batches,
                network_batches=network_batches,
                providers=providers,
            )
        )

    catalog_started = time.perf_counter()
    for query_index, query in enumerate(queries, 1):
        if _cancelled(control):
            was_cancelled = True
            break
        grouped: dict[str, list[Any]] = {}
        provider_done = 0
        if progress:
            progress(
                (query_index - 1) * provider_total,
                total_units,
                f"{query} · requête {query_index}/{len(queries)} · catalogues 0/{provider_total} · {results_seen} résultats",
            )

        if callable(progressive):
            def on_batch(batch: Any) -> None:
                nonlocal provider_done, cache_batches, network_batches, results_seen, was_cancelled
                if _cancelled(control):
                    was_cancelled = True
                    return
                provider_id = str(getattr(batch, "provider_id", "") or f"provider-{provider_done + 1}")
                rows = list(getattr(batch, "results", ()) or ())
                grouped.setdefault(provider_id, []).extend(rows)
                provider_done += 1
                results_seen += len(rows)
                ingest_provisional(rows)
                if bool(getattr(batch, "cached", False)):
                    cache_batches += 1
                else:
                    network_batches += 1
                warnings.extend(str(x) for x in tuple(getattr(batch, "warnings", ()) or ()))
                error = str(getattr(batch, "error_message", "") or "").strip()
                if error:
                    warnings.append(f"{provider_id}: {error}")
                if progress:
                    done = (query_index - 1) * provider_total + min(provider_done, provider_total)
                    progress(
                        max(0, done - 1),
                        total_units,
                        f"{query} · requête {query_index}/{len(queries)} · catalogues {min(provider_done, provider_total)}/{provider_total} · "
                        f"{results_seen} résultats · cache {cache_batches} / réseau {network_batches}",
                    )
                emit_partial(query_index, provider_done)

            try:
                summary = progressive(
                    query,
                    scope=scope,
                    force=False,
                    on_batch=on_batch,
                    control=control,
                )
                warnings.extend(str(x) for x in tuple(getattr(summary, "warnings", ()) or ()))
                if bool(getattr(summary, "cancelled", False)):
                    was_cancelled = True
            except Exception as exc:
                if _cancelled(control):
                    was_cancelled = True
                else:
                    warnings.append(f"{query}: {exc}")
            batches.append(_merge_provider_rows(grouped, providers)[: max(10, int(per_query))])
        else:
            try:
                rows = list(catalog_broker.search(query, scope=scope, force=False) or [])
                results_seen += len(rows)
                cache_unknown_calls += 1
                ingest_provisional(rows)
                batches.append(rows[: max(10, int(per_query))])
                emit_partial(query_index, provider_total)
            except Exception as exc:
                if _cancelled(control):
                    was_cancelled = True
                else:
                    warnings.append(f"{query}: {exc}")
                batches.append([])

        if progress and not was_cancelled:
            progress(
                query_index * provider_total - 1,
                total_units,
                f"{query} · requête {query_index}/{len(queries)} terminée · {results_seen} résultats · "
                f"cache {cache_batches} / réseau {network_batches}",
            )
        if was_cancelled or _cancelled(control):
            was_cancelled = True
            break

    catalog_ms = (time.perf_counter() - catalog_started) * 1000.0

    if was_cancelled:
        provisional_rows = _provisional_head(provisional, max_results)
        trace_performance(catalog_ms=catalog_ms, cancelled=True)
        return RecommendationResult(
            len(books),
            queries,
            tuple(provisional_rows[:max_results]),
            providers,
            tuple(dict.fromkeys(warnings)),
            cache_batches,
            network_batches,
            cache_unknown_calls,
            True,
            tuple(books),
        )

    if progress:
        progress(total_units, total_units, "")
    rank_started = time.perf_counter()
    ranked = rank_candidates_prepared(
        profile,
        batches,
        limit=max_results,
        exploration=exploration,
        score_cache=score_cache,
        exclude_owned=exclude_owned,
    )
    rank_ms = (time.perf_counter() - rank_started) * 1000.0
    trace_performance(catalog_ms=catalog_ms, rank_ms=rank_ms)
    return RecommendationResult(
        len(books),
        queries,
        tuple(ranked),
        providers,
        tuple(dict.fromkeys(warnings)),
        cache_batches,
        network_batches,
        cache_unknown_calls,
        False,
        tuple(books),
    )
