from __future__ import annotations

from dataclasses import dataclass
import re
from types import SimpleNamespace
from typing import Any, Callable

from recommendation_engine import (
    author_values,
    build_profile,
    build_queries,
    candidate_key,
    get_field,
    identifier_values,
    is_already_present,
    normalize,
)
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


def _normalized_authors(book: Any) -> set[str]:
    out: set[str] = set()
    for value in author_values(book):
        for part in re.split(r"[;,/]+", str(value)):
            key = normalize(part)
            if key:
                out.add(key)
    return out


def is_reference_candidate(reference: Any, candidate: Any) -> bool:
    reference_ids = identifier_values(reference)
    candidate_ids = identifier_values(candidate)
    if reference_ids and candidate_ids:
        return bool(reference_ids.intersection(candidate_ids))

    reference_title = normalize(get_field(reference, "title", ""))
    candidate_title = normalize(get_field(candidate, "title", ""))
    if not reference_title or reference_title != candidate_title:
        return False

    reference_authors = _normalized_authors(reference)
    candidate_authors = _normalized_authors(candidate)
    if reference_authors and candidate_authors:
        return reference_authors == candidate_authors
    return not reference_authors and not candidate_authors


class _ReferenceCatalog:
    def __init__(
        self,
        catalog: Any,
        reference: Any,
        *,
        owned_profile: Any = None,
        exclude_owned: bool = True,
    ) -> None:
        self.catalog = catalog
        self.reference = reference
        self.owned_profile = owned_profile
        self.exclude_owned = bool(exclude_owned)

    def searchable_providers(self):
        return self.catalog.searchable_providers()

    def _filtered(self, rows: Any) -> list[Any]:
        return [
            row
            for row in list(rows or [])
            if not is_reference_candidate(self.reference, row)
            and not (
                self.exclude_owned
                and self.owned_profile is not None
                and is_already_present(self.owned_profile, row)
            )
        ]

    def search(self, query: str, **kwargs: Any):
        return self._filtered(self.catalog.search(query, **kwargs))

    def __getattr__(self, name: str):
        if name != "search_progressive":
            return getattr(self.catalog, name)
        progressive = getattr(self.catalog, "search_progressive", None)
        if not callable(progressive):
            raise AttributeError(name)

        def wrapped(query: str, *, on_batch, **kwargs: Any):
            def filtered_batch(batch: Any) -> None:
                on_batch(
                    SimpleNamespace(
                        provider_id=getattr(batch, "provider_id", ""),
                        results=tuple(self._filtered(getattr(batch, "results", ()))),
                        warnings=tuple(getattr(batch, "warnings", ()) or ()),
                        error_message=str(getattr(batch, "error_message", "") or ""),
                        cached=bool(getattr(batch, "cached", False)),
                    )
                )

            return progressive(query, on_batch=filtered_batch, **kwargs)

        return wrapped


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
    owned_books: tuple[Any, ...] | list[Any] = (),
    exclude_owned: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    partial: Callable[[RecommendationPartial], None] | None = None,
    control: Any = None,
) -> RecommendationResult:
    """Compute recommendations using only the selected book as profile seed."""

    plan = prepare_reference(book, max_queries=max_queries)
    owned_profile = build_profile(list(owned_books)) if owned_books else None
    return compute_recommendations(
        _ReferenceMetadata(plan.book),
        _ReferenceCatalog(
            catalog_broker,
            plan.book,
            owned_profile=owned_profile,
            exclude_owned=exclude_owned,
        ),
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
