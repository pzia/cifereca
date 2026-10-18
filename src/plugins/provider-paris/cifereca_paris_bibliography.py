"""Project *explicit* bibliographic facets from Paris search JSON.

The Paris public catalogue displays subjects, collections and audience, but
the exact search-API keys have NOT been proven by captured upstream JSON.
This adapter therefore only consumes already-present, explicitly named keys.
It performs no HTML fetch, no field guessing, no edition ISBN mapping and no
reading-age inference.
"""
from __future__ import annotations

from collections.abc import Mapping
from cifereca.plugin_api import description_text

_FIELDS: dict[str, tuple[str, ...]] = {
    "subjects": ("Subjects", "Subject", "Sujets", "Sujet", "SubjectHeadings"),
    "collections": ("Collections", "Collection"),
    "series_names": ("Series", "Serie", "Série", "SeriesTitle"),
    "contributors": ("Contributors", "Contributor", "Contributeurs"),
    "audiences": ("Audience", "PublicVise", "PublicVisé", "Public"),
}
# Literal audience *categories*, never an age or suitability judgment.
_AUDIENCE = {"jeunesse", "adulte", "tout public", "tous publics"}
_MAX_PER_FACET = 12
_MAX_TEXT = 240


def _texts(raw: object) -> tuple[str, ...]:
    """Only bounded, public bibliographic strings; no arbitrary dict coercion."""
    if isinstance(raw, str):
        values = (raw,)
    elif isinstance(raw, (tuple, list)):
        values = raw[:_MAX_PER_FACET + 1]
    else:
        return ()
    output: list[str] = []
    for entry in values:
        if not isinstance(entry, str) or len(entry) > 2048:
            continue
        text = description_text(entry)
        if not isinstance(text, str):
            continue
        text = " ".join(text.split())
        if not text or len(text) > _MAX_TEXT or any(ord(c) < 32 or ord(c) == 127 for c in text):
            continue
        output.append(text)
        if len(output) >= _MAX_PER_FACET:
            break
    return tuple(output)


def project_bibliographic_facets(
    resource: Mapping[str, object], raw: Mapping[str, object],
) -> dict[str, object]:
    """Build plain JSON-safe facets and per-field evidence for known keys only.

    The same field emitted by two upstream containers is not silently
    overwritten; distinct values are preserved with both evidence paths.
    """
    additional = raw.get("AdditionalProperties")
    sources = (("Resource", resource),)
    if isinstance(additional, Mapping):
        sources += (("AdditionalProperties", additional),)

    metadata: dict[str, object] = {}
    provenance: dict[str, list[str]] = {}
    for facet, keys in _FIELDS.items():
        accepted: list[str] = []
        seen: set[str] = set()
        evidence: list[str] = []
        for location, data in sources:
            for key in keys:
                if key not in data:
                    continue
                values = _texts(data[key])
                if facet == "audiences":
                    values = tuple(v for v in values if v.casefold() in _AUDIENCE)
                if not values:
                    continue
                recorded = False
                for value in values:
                    folded = value.casefold()
                    if folded in seen or len(accepted) >= _MAX_PER_FACET:
                        continue
                    accepted.append(value)
                    seen.add(folded)
                    recorded = True
                if recorded:
                    evidence.append(f"{location}.{key}")
        if accepted:
            metadata[facet] = accepted
            provenance[facet] = evidence
            if facet == "series_names" and len(accepted) == 1:
                metadata["series"] = accepted[0]
            if facet == "audiences" and len(accepted) == 1:
                metadata["audience"] = accepted[0]
    if provenance:
        metadata["bibliography_evidence"] = provenance
    return metadata
