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
# DetailField labels are owned by book_detail_metadata, distinct from the
# multi-source prose labels above (Sujets -> Sujet, Genres -> Genre, etc.).
_CLASSIFICATION_DETAIL_LABELS = (
    ("subjects", "Sujet"), ("subject", "Sujet"),
    ("genres", "Genre"), ("genre", "Genre"),
    ("keywords", "Mot-clé"), ("tags", "Mot-clé"),
    ("themes", "Thème"),
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


def _alternative_proven_fields(alternative, hit, index, provider):
    """Only qualify field values tied to the immutable exact source hit."""
    from datetime import datetime, timezone
    from ..plugin_api import Provenance

    if hit is None:
        return {}
    for name, expected in (("provider_id", hit.provider_id),
                           ("record_id", hit.record_id),
                           ("source_revision", hit.source_revision),
                           ("title", hit.title)):
        value = alternative.get(name)
        if type(value) is not type(expected) or value != expected:
            return {}
    try:
        observed = datetime.fromtimestamp(hit.observed_at, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError, TypeError):
        return {}
    fields = {}
    for name, label, proof, values in (
        ("series", "Série", hit.series_relation,
         [hit.series_relation.label] if hit.series_relation is not None else []),
        ("languages", "Langue", hit.language_observation,
         list(hit.language_observation.languages) if hit.language_observation is not None else []),
    ):
        supplied = alternative.get(name)
        if (proof is None or type(supplied) is not list
                or not 1 <= len(supplied) <= 8
                or any(type(value) is not str for value in supplied)
                or supplied != values
                or _values(supplied, limit=512) != tuple(values)):
            continue
        fields[name] = tuple(DetailField(
            id=f"known-public-alternative-{index}-{name}-{number}",
            label=f"Source {index} · {provider} · {label}", value=value,
            provenance=Provenance(
                source=proof.source + " · " + proof.source_field,
                provider_id=hit.provider_id, source_id=hit.record_id,
                observed_at=observed,
            ),
        ) for number, value in enumerate(values, 1))
    # Classifications are separate independently attested source facts.
    # An edited subject must not remove provenance from a genuine genre.
    from .search_known_public_c_age import _same_native
    classification = hit.classification.wire() if hit.classification is not None else {}
    for name, label in _CLASSIFICATION_DETAIL_LABELS:
        if name not in classification or not _same_native(alternative.get(name), classification[name]):
            continue
        values = _values(classification[name], limit=512)
        if not values:
            continue
        fields[name] = tuple(DetailField(
            id=f"known-public-alternative-{index}-{name}-{number}",
            label=f"Source {index} · {provider} · {label}", value=value,
            provenance=Provenance(
                source="Notice C · observation de source",
                provider_id=hit.provider_id, source_id=hit.record_id,
                observed_at=observed,
            ),
        ) for number, value in enumerate(values[:8], 1))
    age = hit.age_observation
    if age is not None:
        from .search_known_public_c_age import _same_native
        raw_evidence = alternative.get("bibliography_evidence")
        supplied = alternative.get("youth_age_ranges")
        verified = age.filter_ranges()
        # The source's original bounded parser observation and its admitted
        # filter ranges must BOTH match. Never show an age inferred from a
        # different provider, conflicting alternatives or edited metadata.
        if (verified and len(verified) <= 16
                and type(raw_evidence) is dict
                and _same_native(raw_evidence.get("youth_age"), age.wire())
                and _same_native(supplied, verified)):
            labels = {
                "interest": "intérêt", "reading": "lecture",
                "publisher": "éditeur", "recommended": "recommandé",
            }
            fields["youth_age_ranges"] = tuple(DetailField(
                id=f"known-public-alternative-{index}-age-{number}",
                label=f"Source {index} · {provider} · Âge ({labels[part['age_kind']]})",
                value=part["source_label"],
                provenance=Provenance(
                    source=age.source_field,
                    provider_id=hit.provider_id, source_id=hit.record_id,
                    observed_at=observed,
                ),
            ) for number, part in enumerate(verified, 1))
    return fields


def _alternative_summary_provenance(alternative, hit, displayed_names):
    """Attest only the exact displayed source facts, not individual parser fields.

    The summary may contain excerpts; the source-level receipt must match
    the complete underlying values for every displayed entry. Untyped
    passive descriptions remain visible but never acquire this provenance.
    """
    from datetime import datetime, timezone
    from ..core.notice_c_known_projection import _source_alternative
    from ..plugin_api import Provenance

    if hit is None or not displayed_names:
        return None
    expected = _source_alternative(hit)
    for name in ("provider_id", "record_id", "source_revision", "title", *displayed_names):
        value, original = alternative.get(name), expected.get(name)
        if type(value) is not type(original):
            return None
        if type(value) is list:
            if (len(value) != len(original)
                    or any(type(part) is not str for part in value)):
                return None
        elif type(value) not in (str, type(None)):
            return None
        if value != original:
            return None
    try:
        observed = datetime.fromtimestamp(hit.observed_at, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError, TypeError):
        return None
    return Provenance(
        source="Notice C · observation de source",
        provider_id=hit.provider_id, source_id=hit.record_id,
        observed_at=observed,
    )


def known_public_alternative_section(metadata, *, item=None):
    """Keep a separate passive summary for each matching C source."""
    if not _native_mapping(metadata):
        return None
    alternatives = metadata.get("notice_c_source_alternatives")
    if type(alternatives) is not list or not 1 <= len(alternatives) <= 31:
        return None
    from ..core.notice_c_known_projection import known_public_alternative_facts
    hits = known_public_alternative_facts(item)
    if type(item) is not dict or item.get("metadata") is not metadata or len(hits) != len(alternatives):
        hits = ()
    return _render_known_public_alternatives(alternatives, hits)


def _render_known_public_alternatives(alternatives, hits, *, catalogue_sources=()):
    """Shared CoreUI projection of already validated alternative facts."""
    if (type(alternatives) is not list or not 1 <= len(alternatives) <= 31
            or type(hits) is not tuple or (hits and len(hits) != len(alternatives))
            or type(catalogue_sources) is not tuple or len(catalogue_sources) > 128
            or any(type(value) is not str for value in catalogue_sources)):
        return None
    fields = []
    for index, alternative in enumerate(alternatives, 2):
        if not _native_mapping(alternative):
            continue
        provider = alternative.get("provider_id")
        if (type(provider) is not str
                or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", provider)
                or (catalogue_sources and provider not in catalogue_sources)):
            continue
        proven = _alternative_proven_fields(
            alternative, hits[index - 2] if hits else None, index, provider,
        )
        parts = []
        displayed_names = []
        for name, label in _ALTERNATIVE_FIELDS:
            if name in proven:
                continue
            values = _values(alternative.get(name), limit=8192)
            if not values:
                continue
            excerpts = [part[:512] + ("…" if len(part) > 512 else "")
                        for part in values[:8]]
            if len(values) > 8:
                excerpts.append("…")
            parts.append(label + " : " + " ; ".join(excerpts))
            displayed_names.append(name)
        if parts:
            fields.append(DetailField(
                id=f"known-public-alternative-{index}",
                label=f"Source {index} · {provider}", value="\n".join(parts),
                provenance=_alternative_summary_provenance(
                    alternative, hits[index - 2] if hits else None, displayed_names,
                ),
            ))
        for name in ("series", "languages", "youth_age_ranges",
                     *(name for name, _ in _CLASSIFICATION_DETAIL_LABELS)):
            fields.extend(proven.get(name, ()))
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
    # A displayed C field can be attributed only when its exact native
    # value still agrees with the Core hit. Never execute supplier-defined
    # __eq__ while decorating a mutable view row.
    from .search_known_public_c_age import _same_native
    proven = {}
    series = hit.series_relation
    if series is not None and _same_native(metadata.get("series"), [series.label]):
        proven[("Série", series.label)] = series
    language = hit.language_observation
    if language is not None and _same_native(metadata.get("languages"), list(language.languages)):
        for value in language.languages:
            proven[("Langue", value)] = language
    # Each classified subject/genre/tag is a separate SOURCE observation,
    # not a reconciled truth or a field-path from the supplier parser.
    # Protect independent fields from another field's late mutation.
    classified = set()
    classifications = hit.classification.wire() if hit.classification is not None else {}
    for name, label in _CLASSIFICATION_DETAIL_LABELS:
        if name in classifications and _same_native(
            metadata.get(name), classifications[name],
        ):
            classified.update(
                (label, value) for value in _values(classifications[name], limit=4096)
            )
    # The primary source's plain catalogue fields carry source-level
    # provenance, not a fabricated parser field-path. Never decorate local
    # metadata or a changed value merely because its label matches.
    catalogue = {}
    for label, supplied, expected in (
        ("Format", item.get("media_format"), hit.media_format),
        ("Éditeur", metadata.get("publisher"), hit.publisher),
        ("Publication", item.get("published_at"), hit.published_at),
    ):
        if type(supplied) is str and type(expected) is str and supplied == expected:
            catalogue[label] = expected

    def annotate(field, section_title):
        if type(field.value) is not str or field.provenance is not None:
            return field
        proof = proven.get((field.label, field.value))
        if (proof is not None and section_title in
                ("Détail", "Essentiel", "Catalogue")):
            source = proof.source + " · " + proof.source_field
        elif (section_title in ("Détail", "Essentiel")
              and (field.label, field.value) in classified):
            source = "Notice C · observation de source"
        elif section_title == "Catalogue" and catalogue.get(field.label) == field.value:
            source = "Notice C · observation de source"
        else:
            return field
        return replace(field, provenance=Provenance(
            source=source,
            provider_id=hit.provider_id,
            source_id=hit.record_id,
            observed_at=observed_at,
        ))

    return replace(presentation, sections=tuple(
        replace(section, fields=tuple(annotate(field, section.title) for field in section.fields))
        for section in presentation.sections
    ))
