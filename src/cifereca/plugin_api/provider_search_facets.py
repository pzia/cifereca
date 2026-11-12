"""Optional, per-call descriptive catalogue search facets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol, runtime_checkable

from ..models import SearchResult


@dataclass(frozen=True, slots=True)
class CatalogSearchFacetValue:
    label: str
    count: int
    is_selected: bool
    display_label: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogSearchFacet:
    """A descriptor for a single upstream response page and scenario.

    Counts describe the upstream response. They must not be applied to
    individual results or to the locally filtered/deduplicated result list.
    None means values are unverified. No remote filter is established.
    """

    scenario: str
    page: int
    field: str
    label: str
    values: tuple[CatalogSearchFacetValue, ...] | None = None
    filter_capability: ClassVar[Literal["unverified"]] = "unverified"


@dataclass(frozen=True, slots=True)
class PublicSearchNotice:
    """Explicit public-parser output for one exact search row, contract v1.

    The producer must build this from public catalogue fields, never by
    relabelling a private inventory row. This declaration is descriptive,
    not permission to consult holdings or acquire anything. Core validates
    coordinates and rebuilds a non-authorizing row before publication.
    """

    result: SearchResult
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class ProviderSearchResponse:
    results: list[SearchResult]
    facets: tuple[CatalogSearchFacet, ...] = ()
    partial: bool = False
    # Additive: old positional construction and list-only search still work.
    # Absence means no attested public projection, never "all rows public".
    public_notices: tuple[PublicSearchNotice, ...] = ()


@runtime_checkable
class ProviderSearchWithFacets(Protocol):
    """Return rows and facets from the same call, including empty pages.

    Implementations must collect facets and coverage locally to the call.
    Reading a previous response from mutable session diagnostics cannot
    attest scope.
    This optional method supplements the existing list-only search contract.
    """

    def search_with_response_facets(
        self, query: str, scope: str = "all", *, field: str = "all",
        content_types: tuple[str, ...] = (),
    ) -> ProviderSearchResponse: ...
