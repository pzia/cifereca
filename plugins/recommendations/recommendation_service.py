from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from recommendation_engine import RankedCandidate, build_queries, rank_candidates


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


def _merge_provider_rows(grouped: dict[str, list[Any]], providers: tuple[tuple[str, str], ...]) -> list[Any]:
    """Interleave provider rankings so completion speed never biases the top N."""
    order = [str(pid) for pid, _label in providers if str(pid) in grouped]
    order.extend(sorted(pid for pid in grouped if pid not in order))
    maximum = max((len(grouped[pid]) for pid in order), default=0)
    return [grouped[pid][rank] for rank in range(maximum) for pid in order if rank < len(grouped[pid])]


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
    progress: Callable[[int, int, str], None] | None = None,
) -> RecommendationResult:
    # scan=False is intentional: recommendations consume the persisted Library
    # projection and never trigger a filesystem scan.
    books = list(metadata_broker.library_entries(identity_id=identity_id, scan=False) or [])
    queries = build_queries(books, max_queries=max_queries)
    providers = tuple(catalog_broker.searchable_providers() or ())
    if not books or not queries or not providers:
        return RecommendationResult(len(books), queries, (), providers)

    batches: list[list[Any]] = []
    warnings: list[str] = []
    cache_batches = network_batches = cache_unknown_calls = results_seen = 0
    provider_total = max(1, len(providers))
    total_units = max(1, len(queries) * provider_total)
    progressive = getattr(catalog_broker, "search_progressive", None)

    for query_index, query in enumerate(queries, 1):
        grouped: dict[str, list[Any]] = {}
        provider_done = 0
        if progress:
            progress((query_index - 1) * provider_total, total_units,
                     f"{query} · requête {query_index}/{len(queries)} · catalogues 0/{provider_total} · {results_seen} résultats")

        if callable(progressive):
            def on_batch(batch: Any) -> None:
                nonlocal provider_done, cache_batches, network_batches, results_seen
                provider_id = str(getattr(batch, "provider_id", "") or f"provider-{provider_done + 1}")
                rows = list(getattr(batch, "results", ()) or ())
                grouped.setdefault(provider_id, []).extend(rows)
                provider_done += 1
                results_seen += len(rows)
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
                    progress(max(0, done - 1), total_units,
                             f"{query} · requête {query_index}/{len(queries)} · catalogues {min(provider_done, provider_total)}/{provider_total} · "
                             f"{results_seen} résultats · cache {cache_batches} / réseau {network_batches}")

            try:
                summary = progressive(query, scope=scope, force=False, on_batch=on_batch)
                warnings.extend(str(x) for x in tuple(getattr(summary, "warnings", ()) or ()))
                if bool(getattr(summary, "cancelled", False)):
                    warnings.append(f"{query}: recherche annulée")
            except Exception as exc:
                warnings.append(f"{query}: {exc}")
            rows = _merge_provider_rows(grouped, providers)
            batches.append(rows[: max(10, int(per_query))])
        else:
            # Compatibility path. Core 0.9.44 normally uses search_progressive;
            # cache provenance cannot be observed through legacy search().
            try:
                rows = list(catalog_broker.search(query, scope=scope, force=False) or [])
                results_seen += len(rows)
                cache_unknown_calls += 1
                batches.append(rows[: max(10, int(per_query))])
            except Exception as exc:
                warnings.append(f"{query}: {exc}")
                batches.append([])

        if progress:
            progress(query_index * provider_total - 1, total_units,
                     f"{query} · requête {query_index}/{len(queries)} terminée · {results_seen} résultats · "
                     f"cache {cache_batches} / réseau {network_batches}")

    if progress:
        progress(total_units, total_units, "")
    ranked = rank_candidates(books, batches, limit=max_results, exploration=exploration)
    return RecommendationResult(
        len(books), queries, tuple(ranked), providers, tuple(dict.fromkeys(warnings)),
        cache_batches, network_batches, cache_unknown_calls,
    )
