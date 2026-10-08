from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from recommendation_engine import build_profile, build_queries, candidate_key
from recommendation_service import (
    RecommendationPartial,
    RecommendationResult,
    compute_recommendations,
)


@dataclass(frozen=True)
class ReferencePlan:
    """Prepared signals for a recommendation centered on one concrete book."""

    book: Any
    key: str
    queries: tuple[str, ...]


class _ReferenceMetadata:
    """Minimal metadata broker exposing only the selected reference book."""

    def __init__(self, book: Any) -> None:
        self.book = book

    def library_entries(self, **_kwargs: Any) -> list[Any]:
        return [self.book]


def prepare_reference(book: Any, *, max_queries: int = 8) -> ReferencePlan:
    """Build recommendation signals without depending on a Core BookRef type."""

    if book is None:
        raise ValueError("Livre de référence manquant")
    profile = build_profile([book])
    queries = build_queries([book], max_queries=max_queries, profile=profile)
    if not queries:
        raise ValueError(
            "Le livre de référence ne contient pas assez de métadonnées "
            "pour construire une recommandation"
        )
    return ReferencePlan(book=book, key=candidate_key(book), queries=queries)


def compute_reference_recommendations(
    catalog_broker: Any,
    book: Any,
    *,
    scope: str = "digital",
    max_queries: int = 8,
    per_query: int = 50,
    max_results: int = 120,
    exploration: int = 25,
    progress: Callable[[int, int, str], None] | None = None,
    partial: Callable[[RecommendationPartial], None] | None = None,
    control: Any = None,
) -> RecommendationResult:
    """Compute recommendations using only the selected book as profile seed."""

    plan = prepare_reference(book, max_queries=max_queries)
    return compute_recommendations(
        _ReferenceMetadata(plan.book),
        catalog_broker,
        "book-reference",
        scope=scope,
        max_queries=max_queries,
        per_query=per_query,
        max_results=max_results,
        exploration=exploration,
        progress=progress,
        partial=partial,
        control=control,
    )
