"""Presentation-only bibliography of an already pinned known public Search C.

Reuse the common detail metadata vocabulary, never flatten other source
descriptions into the primary source's searchable fields. The caller owns
the exact row/epoch/identity receipt; no SQLite, network or permissions.
"""
from __future__ import annotations

import re

from ..core.public_search_projection import _public_bibliographic_text
from ..plugin_api import DetailField, DetailSection

_FIELDS = (
    ("series", "Série"), ("languages", "Langues"),
    ("subjects", "Sujets"), ("subject", "Sujet"),
    ("genres", "Genres"), ("genre", "Genre"),
    ("keywords", "Mots-clés"), ("themes", "Thèmes"), ("tags", "Tags"),
)
_ALTERNATIVE_FIELDS = (
    ("title", "Titre"), ("author", "Auteur"), ("publisher", "Éditeur"),
    ("published_at", "Publication"), *_FIELDS, ("description", "Résumé (extrait)"),
)


def _native_mapping(value):
    return (type(value) is dict and len(value) <= 64
            and all(type(key) is str for key in value))


def _values(value, *, limit):
    raw = (value,) if type(value) is str else value if type(value) is list else ()
    if len(raw) > 32:
        return ()
    return tuple(part for part in raw if type(part) is str
                 and _public_bibliographic_text(part, required=True, limit=limit))


def known_public_detail_metadata(metadata):
    """Only the primary source's already declared bibliographic facts."""
    if not _native_mapping(metadata):
        return {}
    facts = {}
    for name, _ in _FIELDS:
        values = _values(metadata.get(name), limit=4096)
        if values:
            facts[name] = list(values)
    return facts


def known_public_alternative_section(metadata):
    """Keep a separate passive summary for each matching C source."""
    if not _native_mapping(metadata):
        return None
    alternatives = metadata.get("notice_c_source_alternatives")
    if type(alternatives) is not list or not 1 <= len(alternatives) <= 31:
        return None
    fields = []
    for index, alternative in enumerate(alternatives, 2):
        if not _native_mapping(alternative):
            continue
        provider = alternative.get("provider_id")
        if (type(provider) is not str
                or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", provider)):
            continue
        parts = []
        for name, label in _ALTERNATIVE_FIELDS:
            values = _values(alternative.get(name), limit=8192)
            if not values:
                continue
            excerpts = [part[:512] + ("…" if len(part) > 512 else "")
                        for part in values[:8]]
            if len(values) > 8:
                excerpts.append("…")
            parts.append(label + " : " + " ; ".join(excerpts))
        if parts:
            fields.append(DetailField(
                id=f"known-public-alternative-{index}",
                label=f"Source {index} · {provider}", value="\n".join(parts),
            ))
    if not fields:
        return None
    return DetailSection(
        "known-public-alternatives", "Autres descriptions de la même notice",
        tuple(fields), 900,
    )


def with_known_public_field_provenance(presentation, item):
    """Annotate only displayed values matching immutable validated C facts."""
    from dataclasses import replace
    from datetime import datetime, timezone
    from ..core.notice_c_known_projection import known_public_primary_facts
    from ..plugin_api import Provenance

    hit = known_public_primary_facts(item)
    if hit is None:
        return presentation
    metadata = item["metadata"]
    try:
        observed_at = datetime.fromtimestamp(hit.observed_at, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError, TypeError):
        return presentation
    proven = {}
    series = hit.series_relation
    if series is not None and metadata.get("series") == [series.label]:
        proven[("Série", series.label)] = series
    language = hit.language_observation
    if language is not None and metadata.get("languages") == list(language.languages):
        for value in language.languages:
            proven[("Langue", value)] = language
    if not proven:
        return presentation

    def annotate(field):
        if type(field.value) is not str or field.provenance is not None:
            return field
        proof = proven.get((field.label, field.value))
        if proof is None:
            return field
        return replace(field, provenance=Provenance(
            source=proof.source + " · " + proof.source_field,
            provider_id=hit.provider_id,
            source_id=hit.record_id,
            observed_at=observed_at,
        ))

    return replace(presentation, sections=tuple(
        replace(section, fields=tuple(annotate(field) for field in section.fields))
        for section in presentation.sections
    ))
