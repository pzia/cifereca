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
from unicodedata import category

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


def _series_link_declared(href: object) -> bool:
    """Recognize only the already qualified index names, even on bad links."""
    return (type(href) is str and 0 < len(href) <= 32768
            and bool(_SERIES_TOKEN.search(unquote(html.unescape(href)))))


def _safe_series_search_href(href: object) -> bool:
    """Require the existing internal-link shape; never follow the link."""
    if type(href) is not str or not 1 <= len(href) <= 2048:
        return False
    decoded = unquote(html.unescape(href))
    if (not _series_link_declared(href)
            or any(ch.isspace() or category(ch) in {"Cc", "Cf", "Cs"} or ch == chr(92)
                   for ch in href)
            or any(category(ch) in {"Cc", "Cf", "Cs"} or ch == chr(92)
                   for ch in decoded)):
        return False
    try:
        # Recheck the decoded representation as well: percent-encoded
        # authority separators, fragments and credentials cannot qualify.
        for value in (href, decoded):
            parsed = urlsplit(value)
            if parsed.scheme:
                if (parsed.scheme != "https" or parsed.hostname not in _SERIES_ALLOWED_HOSTS
                        or parsed.username is not None or parsed.password is not None
                        or parsed.port not in (None, 443)):
                    return False
            elif parsed.netloc or not parsed.path.startswith(("/", "./", "../")):
                return False
            if parsed.fragment:
                return False
    except (ValueError, UnicodeError):
        return False
    return True


class _SeriesAnchorParser(HTMLParser):
    """Distinguish an absent relation from rejected/contradictory evidence."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._inside = False
        self._parts: list[str] = []
        self._size = 0
        self._invalid = False
        self._count = 0
        self._values: list[str] = []
        self._rejected = False
        self._active = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "noscript", "textarea"}:
            if self._inside:
                self._invalid = True
            if self._active is None:
                self._active = tag
            return
        if tag == "a":
            self._count += 1
            if self._count > 64 or self._inside:
                self._rejected = True
            hrefs = [value for key, value in attrs if key == "href"]
            declared = any(_series_link_declared(href) for href in hrefs)
            self._inside = declared
            self._parts = []
            self._size = 0
            self._invalid = (declared and (
                self._active is not None
                or len(hrefs) != 1 or not _safe_series_search_href(hrefs[0])
            ))
        elif self._inside and tag not in {"span", "b", "i", "em", "strong"}:
            self._invalid = True

    def handle_data(self, data):
        if self._inside:
            self._size += len(data)
            if self._size > 1024:
                self._invalid = True
            elif not self._invalid:
                self._parts.append(data)

    def handle_endtag(self, tag):
        if tag == self._active:
            self._active = None
        if tag == "a":
            if self._inside:
                label = None if self._invalid else _clean_text("".join(self._parts))
                if (label is not None and "://" not in label and "@" not in label
                        and not any(s in label.casefold() for s in (
                            "token=", "password=", "secret=", "bearer ", "cookie:",
                        ))):
                    self._values.append(label)
                else:
                    self._rejected = True
            self._inside = False
            self._parts = []
            self._size = 0

    def result(self) -> tuple[str, str | None]:
        if self._rejected or self._inside:
            return "rejected", None
        unique = {text.casefold(): text for text in self._values}
        if len(unique) > 1:
            return "conflict", None
        if not unique:
            return "absent", None
        return "valid", next(iter(unique.values()))


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
        state, label = parser.result()
        if state in {"rejected", "conflict"}:
            return {}
        if state == "valid" and label is not None:
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



def public_search_series_metadata(
    metadata: object, scenario: str,
) -> dict[str, object]:
    """Copy only an already parsed result-owned series and its exact proof.

    This is a public declaration projection, not another parser. The caller
    owns the anonymous Search response. No raw HTML, page facet, account
    metadata, URL, age or right is carried into the declaration.
    """
    if type(metadata) is not dict or scenario not in {"CATALOGUE", "EBOOKS"}:
        return {}
    values = metadata.get("series")
    claims = metadata.get("bibliography_evidence")
    if type(values) is not list or len(values) != 1 or type(claims) is not dict:
        return {}
    proof = claims.get("series")
    if type(proof) is not dict:
        return {}
    source_field = proof.get("source_field")
    if source_field not in ("Search.CustomResult", "Search.CompactResult"):
        return {}
    expected = {
        "source": "paris.syracuse.search",
        "source_field": source_field,
        "scope": scenario,
        "confidence": "linked_search_relation",
        "freshness": "search_response",
    }
    label = values[0]
    if (proof != expected or type(label) is not str
            or _safe_facet_choice_text(label) != label
            or re.match(r"(?i)^(?:~|file:|[a-z]:[/\\\\])", label)):
        return {}
    return {
        "series": [label],
        "bibliography_evidence": {"series": expected},
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
