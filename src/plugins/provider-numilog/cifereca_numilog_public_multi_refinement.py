"""Evidence-checked local refinement across explicitly loaded Numilog pages.

This module NEVER issues HTTP requests or visits another page automatically.
The caller supplies a small, contiguous set of public Numilog search pages
previously obtained using browse_public_search/follow_public_link. The total
always refers to the *unfiltered public search*, not these local matches.
"""
from __future__ import annotations

from dataclasses import dataclass

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import PublicSearchPage, PublicSearchParseError, _query
from cifereca_numilog_public_local_filter import refine_loaded_public_page

_MAX_LOADED_PAGES = 8
_ERROR = "Numilog public : unverified_loaded_pages"


@dataclass(frozen=True)
class PublicPagesRefinement:
    """Bounded local matches with explicit per-query coverage information."""

    items: tuple
    applied_fields: tuple[str, ...]
    scope: str
    loaded_page_indices: tuple[int, ...]
    loaded_page_count: int
    loaded_count: int
    matching_count: int
    missing_evidence_count: int
    public_unfiltered_total: int
    public_results_fully_loaded: bool
    has_uninspected_or_unverified_matches: bool


def _source(url):
    """Validate a source/pager URL without exposing its raw value in errors."""
    try:
        parsed = _query(url)
    except (PublicSearchParseError, ValueError, TypeError):
        raise ProviderCapabilityError(_ERROR) from None
    return parsed, {k: v for k, v in parsed.items() if k != "GRID_PAGE"}


def refine_loaded_public_pages(pages, **filters) -> PublicPagesRefinement:
    """Combine only consecutive pages of ONE proven public search/facet state.

    Pagination is not fetched automatically; only already-loaded pages are
    inspected. If there are more public results, or even one unproved field,
    callers MUST retain the non-exhaustive flag, including on zero matches.
    """
    if (type(pages) not in (list, tuple)
            or not 1 <= len(pages) <= _MAX_LOADED_PAGES
            or any(type(p) is not PublicSearchPage for p in pages)):
        raise ProviderCapabilityError(_ERROR)

    matches = []
    unknown = 0
    loaded = 0
    seen = set()
    basis = None
    prior_next_url = None
    total = None
    fields = None
    last = None

    for index, page in enumerate(pages):
        if page.page_index != index or type(page.page_index) is not int:
            raise ProviderCapabilityError(_ERROR)
        # The single-page implementation revalidates each row's canonical
        # public identity, exact selector evidence, rights and filter input.
        local = refine_loaded_public_page(page, **filters)

        if total is None:
            total = page.total_results
            fields = local.applied_fields
        elif page.total_results != total:
            # A changed result count can mean a changing catalogue/query.
            # Do not splice editions from incompatible snapshots together.
            raise ProviderCapabilityError(_ERROR)

        if not page.items:
            if (index != 0 or len(pages) != 1 or total != 0
                    or page.complete is not True or page.next_url is not None
                    or page.pagination_links):
                raise ProviderCapabilityError(_ERROR)
            # A verified empty-result document contains no card source URL.
            # It cannot be used as evidence for any additional page.
        else:
            source_url = page.items[0].metadata["source_url"]
            parsed, scope = _source(source_url)
            if int(parsed.get("GRID_PAGE", "0")) != index:
                raise ProviderCapabilityError(_ERROR)
            if basis is None:
                basis = scope
            if basis != scope:
                raise ProviderCapabilityError(_ERROR)
            if index and prior_next_url != source_url:
                # Only an exact server-issued next link connects the pages:
                # no arbitrary direct page jumps or facet transitions.
                raise ProviderCapabilityError(_ERROR)
            if len(page.items) > total:
                raise ProviderCapabilityError(_ERROR)

        for row in page.items:
            if row.record_id in seen:
                # Duplicates can hide a changed edition or pagination drift;
                # silently de-duplicating would misstate coverage.
                raise ProviderCapabilityError(_ERROR)
            seen.add(row.record_id)

        if page.next_url is not None:
            parsed_next, next_scope = _source(page.next_url)
            if (next_scope != basis
                    or int(parsed_next.get("GRID_PAGE", "0")) != index + 1):
                raise ProviderCapabilityError(_ERROR)
        if index + 1 < len(pages) and page.next_url is None:
            raise ProviderCapabilityError(_ERROR)
        prior_next_url = page.next_url
        last = page
        loaded += local.loaded_count
        unknown += local.missing_evidence_count
        matches.extend(local.items)

    if loaded > total:
        raise ProviderCapabilityError(_ERROR)

    # These pages may be a subset of the source's search. Only an exact
    # unfiltered counter match, contiguous navigation, and no forward pager
    # justify treating the search results as fully loaded.
    forward_pager = False
    if last.pagination_links:
        for link in last.pagination_links:
            if type(link) is not dict or type(link.get("url")) is not str:
                raise ProviderCapabilityError(_ERROR)
            parsed, link_scope = _source(link["url"])
            if link_scope != basis:
                raise ProviderCapabilityError(_ERROR)
            if int(parsed.get("GRID_PAGE", "0")) > last.page_index:
                forward_pager = True
    full = (loaded == total and last.next_url is None and not forward_pager)
    return PublicPagesRefinement(
        items=tuple(matches),
        applied_fields=fields,
        scope="loaded_public_pages_only",
        loaded_page_indices=tuple(range(len(pages))),
        loaded_page_count=len(pages),
        loaded_count=loaded,
        matching_count=len(matches),
        missing_evidence_count=unknown,
        public_unfiltered_total=total,
        public_results_fully_loaded=full,
        has_uninspected_or_unverified_matches=(not full or unknown > 0),
    )
