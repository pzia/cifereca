"""Explicit local refinement of ONE already-loaded Numilog public search page.

Never a server-side author/ISBN search or an exhaustive catalogue query.
An absent field is unknown, not evidence that the item does not match.
No HTTP, account, ownership, signed URLs, DRM or acquisition.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import unicodedata

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog_public import PublicSearchPage, _query, PublicSearchParseError

_PROOF = {
    "title": ("title", "a.TitleBook"),
    "author": ("authors", "a.Author"),
    "publisher": ("publisher", ".product_meta .LabelInfos[Éditeur]"),
    "year": ("published_at", ".product_meta .LabelInfos[Parution]"),
    "format_label": ("format_labels", ".product_meta .LabelInfos[Format(s)]"),
}
_ALLOWED = frozenset(_PROOF)


@dataclass(frozen=True)
class PublicPageRefinement:
    """Matches from this page only; public_total is NOT a filtered total."""

    items: tuple
    applied_fields: tuple[str, ...]
    scope: str
    loaded_count: int
    matching_count: int
    missing_evidence_count: int
    public_unfiltered_total: int
    public_page_index: int
    public_page_complete: bool
    has_uninspected_or_unverified_matches: bool


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(c for c in value
                            if not unicodedata.combining(c)).split())


def _validate_page(page):
    if type(page) is not PublicSearchPage or len(page.items) > 100:
        raise ProviderCapabilityError("Numilog public : unverified_local_page")
    for row in page.items:
        if (getattr(row, "source_provider", None) != "numilog-catalog"
                or getattr(row, "source_credential", None) != "__public__"
                or getattr(row, "source_collection", None) != "public_catalog"
                or getattr(row, "scenario", None) != "NUMILOG_PUBLIC"
                or getattr(row, "docbase", None) != "NUMILOG"
                or getattr(row, "owned", None) is not False
                or getattr(row, "downloadable", None) is not False
                or getattr(row, "download_url", None) is not None):
            raise ProviderCapabilityError("Numilog public : unverified_local_page")
        m = getattr(row, "metadata", None)
        if type(m) is not dict or type(m.get("source_url")) is not str:
            raise ProviderCapabilityError("Numilog public : unverified_local_page")
        try:
            _query(m["source_url"])
        except PublicSearchParseError:
            raise ProviderCapabilityError("Numilog public : unverified_local_page") from None
        if type(m.get("field_evidence")) is not dict:
            raise ProviderCapabilityError("Numilog public : unverified_local_page")


def _value(row, field):
    meta = row.metadata
    key, selector = _PROOF[field]
    proof = meta["field_evidence"].get(key)
    source = meta["source_url"]
    if (type(proof) is not dict or proof != {
            "source_url": source,
            "source": selector,
            "kind": "observed_public_html",
    }):
        return None
    if field == "title":
        value = row.title
    elif field == "author":
        value = meta.get("authors")
    elif field == "publisher":
        value = row.publisher
    elif field == "year":
        value = row.published_at
    else:
        value = meta.get("format_labels")
    if field == "year":
        if type(value) is not str or len(value) != 10:
            return None
        try:
            return date.fromisoformat(value).year
        except ValueError:
            return None
    if field in {"author", "format_label"}:
        if (type(value) is not list or not 1 <= len(value) <= 32
                or any(type(x) is not str or not x or len(x) > 1000 for x in value)):
            return None
        return tuple(value)
    if type(value) is not str or not value or len(value) > 2000:
        return None
    return value


def refine_loaded_public_page(page: PublicSearchPage, **filters) -> PublicPageRefinement:
    """Filter positive, source-attested facts on one page with honest coverage.

    The caller must explicitly browse an existing public Numilog session first.
    This method does not call the network, cannot search an ISBN, and never
    converts absent or unproved metadata into a negative assertion.
    """
    if not filters or set(filters) - _ALLOWED:
        raise ProviderCapabilityError("Numilog public : unsupported_local_filter")
    qualified = {}
    for field, value in filters.items():
        if field == "year":
            if type(value) is not int or not 1000 <= value <= 9999:
                raise ProviderCapabilityError("Numilog public : invalid_local_filter")
            qualified[field] = value
        elif (type(value) is not str or not 1 <= len(value.strip()) <= 120
              or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value)):
            raise ProviderCapabilityError("Numilog public : invalid_local_filter")
        else:
            qualified[field] = _fold(value.strip())
    _validate_page(page)
    found = []
    unverified = 0
    for row in page.items:
        missing_proof = False
        mismatch = False
        for field, expected in qualified.items():
            actual = _value(row, field)
            if actual is None:
                missing_proof = True
                continue
            if field == "year":
                matched = actual == expected
            elif isinstance(actual, tuple):
                matched = any(expected in _fold(item) for item in actual)
            else:
                matched = expected in _fold(actual)
            if not matched:
                mismatch = True
        if not mismatch and missing_proof:
            unverified += 1
        elif not mismatch:
            found.append(deepcopy(row))
    return PublicPageRefinement(
        items=tuple(found), applied_fields=tuple(sorted(qualified)),
        scope="loaded_public_page_only",
        loaded_count=len(page.items), matching_count=len(found),
        missing_evidence_count=unverified,
        public_unfiltered_total=page.total_results,
        public_page_index=page.page_index,
        public_page_complete=page.complete,
        has_uninspected_or_unverified_matches=(not page.complete or unverified > 0),
    )
