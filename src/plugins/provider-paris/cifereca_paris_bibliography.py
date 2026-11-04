"""Attested Syracuse resource-level facets (#3415).

Only field names observed in Paris search JSON are accepted. The presence of a
field in the upstream schema does not imply nonempty data for every record.
This module does not infer editions, ages, search endpoints or loan rights.
"""
from __future__ import annotations

import html
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

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



_SERIES_TOKEN = re.compile(r"(?<![a-z0-9_])SeriesTitle_(?:idx|exact)(?![a-z0-9_])", re.I)
_SERIES_ALLOWED_HOSTS = {"bibliotheques.paris.fr"}


def _safe_series_search_href(href: object) -> bool:
    """Require an internal SearchService series link, not an arbitrary anchor.\n\n    Observed Search snippets use both SeriesTitle_idx and SeriesTitle_exact.

    Only a link supplied inside this *result's own* HTML is a candidate.
    Never fetch/follow its target; it is metadata evidence, not a capability.
    """
    if type(href) is not str or not 1 <= len(href) <= 2048:
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in href):
        return False
    decoded = unquote(html.unescape(href))
    if not _SERIES_TOKEN.search(decoded):
        return False
    try:
        parsed = urlsplit(href)
    except ValueError:
        return False
    if parsed.scheme:
        if (parsed.scheme != "https" or parsed.hostname not in _SERIES_ALLOWED_HOSTS
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)):
            return False
    elif parsed.netloc or not parsed.path.startswith(("/", "./", "../")):
        return False
    return not parsed.fragment


class _SeriesAnchorParser(HTMLParser):
    """Bounded, fail-closed HTML extraction; no script, DOM or active links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._inside = False
        self._parts: list[str] = []
        self._invalid = False
        self._count = 0
        self._values: list[str] = []
        self._rejected = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._count += 1
            if self._count > 64 or self._inside:
                self._rejected = True
            hrefs = [value for key, value in attrs if key == "href"]
            self._inside = len(hrefs) == 1 and _safe_series_search_href(hrefs[0])
            self._parts = []
            self._invalid = False
        elif self._inside and tag not in {"span", "b", "i", "em", "strong"}:
            self._invalid = True

    def handle_data(self, data):
        if self._inside:
            self._parts.append(data)
            if sum(map(len, self._parts)) > 1024:
                self._invalid = True

    def handle_endtag(self, tag):
        if tag == "a":
            if self._inside and not self._invalid:
                label = _clean_text("".join(self._parts))
                if (label is not None and "://" not in label and "@" not in label
                        and not any(s in label.casefold() for s in (
                            "token=", "password=", "secret=", "bearer ", "cookie:",
                        ))):
                    self._values.append(label)
            self._inside = False
            self._parts = []

    def result(self) -> str | None:
        if self._rejected:
            return None
        unique = {text.casefold(): text for text in self._values}
        return next(iter(unique.values())) if len(unique) == 1 else None


def project_attested_search_series(
    result: Mapping[str, object], scenario: str,
) -> dict[str, object]:
    """Project only an unambiguous series anchor from THIS Search result.

    Anchors are evidence of a search relation, not a structured edition
    property, catalogue-server filter or entitlement. Unknown fields remain
    unknown; global FacetCollectionList must never be joined to this book.
    """
    if scenario not in {"CATALOGUE", "EBOOKS"} or not isinstance(result, Mapping):
        return {}
    found: list[tuple[str, str]] = []
    for field in ("CustomResult", "CompactResult"):
        raw = result.get(field)
        if type(raw) is not str or not raw or len(raw) > 32768:
            if raw is not None and (type(raw) is not str or len(raw) > 32768):
                return {}
            continue
        parser = _SeriesAnchorParser()
        try:
            parser.feed(raw)
            parser.close()
        except (ValueError, AssertionError):
            return {}
        label = parser.result()
        if label is not None:
            found.append((label, field))
    if not found or len({value.casefold() for value, _field in found}) != 1:
        return {}
    series, field = found[0]
    return {
        "series": [series],
        "bibliography_evidence": {
            "series": {
                "source": "paris.syracuse.search",
                "source_field": "Search." + field,
                "scope": scenario,
                "confidence": "linked_search_relation",
                "freshness": "search_response",
            },
        },
    }


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



_FACET_VALUES_MAX = 64
_FACET_COUNT_MAX = 1_000_000
_FACET_TEXT_MAX = 160


def _safe_facet_choice_text(value: object) -> str | None:
    """Reject payloads unsafe for UI, diagnostic_state and local traces."""
    text = _clean_text(value)
    if (text is None or len(text) > _FACET_TEXT_MAX or
            "://" in text or "@" in text or "\\" in text or
            text.startswith(("/", ".")) or
            any(marker in text.casefold() for marker in (
                "token=", "password=", "secret=", "cookie:", "bearer ",
                "authorization:", "api_key=", "signature=", "refresh_token=",
            ))):
        return None
    return text


def _attested_response_facet_values(choices: list[object]) -> list[dict[str, object]] | None:
    """All-or-nothing parse of the captured Syracuse FacetList item shape.

    Only Label:str, Count:int, IsSelected:bool, optional DisplayLabel:str
    are evidenced. A malformed item invalidates the WHOLE list: no guessed
    counters, missing items or assertion that a server-side filter works.
    """
    if not 1 <= len(choices) <= _FACET_VALUES_MAX:
        return None
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for choice in choices:
        if type(choice) is not dict:
            return None
        label = _safe_facet_choice_text(choice.get("Label"))
        count = choice.get("Count")
        selected = choice.get("IsSelected")
        if (label is None or label.casefold() in seen
                or type(count) is not int or not 0 <= count <= _FACET_COUNT_MAX
                or type(selected) is not bool):
            return None
        entry: dict[str, object] = {
            "label": label, "count": count, "is_selected": selected,
        }
        if "DisplayLabel" in choice:
            display = _safe_facet_choice_text(choice["DisplayLabel"])
            if display is None:
                return None
            entry["display_label"] = display
        result.append(entry)
        seen.add(label.casefold())
    return result


def project_search_response_facets(
    response: Mapping[str, object], scenario: str, page: int
) -> list[dict[str, object]]:
    """Project *response-level* Syracuse facet descriptors, never book fields.

    Observed upstream keys: d.FacetCollectionList entries carry FacetField,
    FacetLabel, FacetList, IsSearchable and IsMultiselectable. Official
    CATALOGUE/EBOOKS captures now attest a bounded item tuple (Label,
    Count, IsSelected, optional DisplayLabel) at the *response-page* level.
    Even a positive IsSearchable does not prove a working filter endpoint.

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
        if (field is None or label is None or type(choices) is not list
                or len(choices) > 1024 or field.casefold() in seen):
            continue
        seen.add(field.casefold())
        # Provenance/shape of these values is different from Resource fields:
        # do not project them into SearchResult metadata or assume filtering.
        observed_values = _attested_response_facet_values(choices)
        descriptor = {
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
            "values_schema": (
                "label_count_selected_v1" if observed_values is not None
                else "unverified"
            ),
        }
        if observed_values is not None:
            descriptor["values"] = observed_values
        projected.append(descriptor)
        if len(projected) >= 32:
            break
    return projected
