"""Attested Syracuse resource-level facets (#3415).

Only field names observed in Paris search JSON are accepted. The presence of a
field in the upstream schema does not imply nonempty data for every record.
This module does not infer editions, ages, search endpoints or loan rights.
"""
from __future__ import annotations

import html
from collections.abc import Mapping

_MAX_VALUES = 12
_MAX_TEXT = 240
_MAX_INPUT = 2048

# Distinguish subject headings from site/blog taxonomy without pretending
# the latter are server-side searchable bibliographic classifications.
_RESOURCE_FIELDS = {
    "subjects": "Subj",
    "tags": "Tags",
    "paris_blog_categories": "BlogPostCategories",
    "paris_blog_tags": "BlogPostTags",
}


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > _MAX_INPUT:
        return None
    # Refuse rich HTML rather than coercing a nested/active payload to text.
    if "<" in value or ">" in value:
        return None
    value = html.unescape(value)
    if "<" in value or ">" in value:
        return None
    value = " ".join(value.split())
    if not value or len(value) > _MAX_TEXT:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _texts(value: object) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (tuple, list)):
        candidates = value[:_MAX_VALUES + 1]
    else:
        # Real upstream object shapes not yet attested: do not guess keys.
        return []
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = _clean_text(candidate)
        if text is None or text.casefold() in seen:
            continue
        result.append(text)
        seen.add(text.casefold())
        if len(result) >= _MAX_VALUES:
            break
    return result


def project_observed_resource_facets(
    resource: Mapping[str, object], scenario: str
) -> dict[str, object]:
    """Project safe per-record values already received in CATALOGUE/EBOOKS.

    Evidence is scoped to this search response. Neither the response's age
    nor the semantic meaning of a blog taxonomy is assumed to be verified.
    """
    if scenario not in {"CATALOGUE", "EBOOKS"}:
        return {}
    metadata: dict[str, object] = {}
    evidence: dict[str, dict[str, str]] = {}
    for public_name, upstream_field in _RESOURCE_FIELDS.items():
        values = _texts(resource.get(upstream_field))
        if not values:
            continue
        metadata[public_name] = values
        evidence[public_name] = {
            "source": "paris.syracuse.search",
            "source_field": "Resource." + upstream_field,
            "scope": scenario,
            "confidence": "upstream_field_present",
            "freshness": "search_response",
        }
    if evidence:
        metadata["bibliography_evidence"] = evidence
    return metadata


def project_search_response_facets(
    response: Mapping[str, object], scenario: str, page: int
) -> list[dict[str, object]]:
    """Project *response-level* Syracuse facet descriptors, never book fields.

    Observed upstream keys: d.FacetCollectionList entries carry FacetField,
    FacetLabel, FacetList, IsSearchable and IsMultiselectable. The nested
    FacetList item schema has NOT been qualified; do not guess its values,
    their counts across pages, or a working server-side filter endpoint.

    Each emitted descriptor records only the returned page and scenario.
    """
    if scenario not in {"CATALOGUE", "EBOOKS"} or type(page) is not int or page < 0:
        return []
    d = response.get("d")
    if not isinstance(d, dict):
        return []
    raw_facets = d.get("FacetCollectionList")
    if type(raw_facets) is not list or len(raw_facets) > 64:
        return []
    projected: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in raw_facets:
        if type(raw) is not dict:
            continue
        field = _clean_text(raw.get("FacetField"))
        label = _clean_text(raw.get("FacetLabel"))
        choices = raw.get("FacetList")
        # FacetList contents are not parsed until real item keys are attested.
        if (field is None or label is None or type(choices) is not list
                or len(choices) > 1024 or field.casefold() in seen):
            continue
        seen.add(field.casefold())
        projected.append({
            "scenario": scenario,
            "page": page,
            "field": field,
            "label": label,
            "upstream_is_searchable": (
                raw.get("IsSearchable")
                if type(raw.get("IsSearchable")) is bool else None
            ),
            "upstream_is_multiselectable": (
                raw.get("IsMultiselectable")
                if type(raw.get("IsMultiselectable")) is bool else None
            ),
            "observed_entry_count": len(choices),
            "source": "SearchService.d.FacetCollectionList",
            "scope": "response_page_only",
            "filter_capability": "unverified",
            "values_schema": "unverified",
        })
        if len(projected) >= 32:
            break
    return projected
