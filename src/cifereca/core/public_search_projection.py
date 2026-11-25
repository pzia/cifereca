"""Inert admission of explicit public catalogue rows (#5048/#5518).

No supplier grammar, provider call, storage, or permission inference. Caller
retains the live search identity/session/generation lease before publication.
"""
from __future__ import annotations

import re
import json
from dataclasses import asdict, dataclass

from ..models import SearchResult
from ..plugin_api.provider_search_facets import ProviderSearchResponse, PublicSearchNotice
from .notice_source_observation import _safe_scalar

_KEY_FIELDS = (
    "id", "source_provider", "source_credential", "docbase", "record_id",
    "scenario", "source_collection", "medium",
)
_TEXT_FIELDS = (
    "title", "author", "type", "format", "publisher", "published_at",
    "description", "content_type",
)
_BIBLIOGRAPHY = frozenset({
    "language", "languages", "original_language", "isbn", "isbns", "series",
    "collection", "publisher", "subjects", "subject", "genres", "genre",
    "authors", "contributors", "edition", "tags", "keywords", "themes",
    "awards", "prizes", "literary_prizes",
})


def _text(value: object, *, required: bool = False, limit: int = 8192) -> bool:
    return (value is None and not required) or (
        type(value) is str and len(value) <= limit
        and (not required or bool(value.strip())) and _safe_scalar(value)
        and not any(ord(c) < 32 and c not in "\n\r\t" for c in value)
    )


_PRIVATE_BIBLIOGRAPHIC_PATTERN = re.compile(
    r"(?i)(?:https?://|(?<!\w)(?:bearer|basic)\s+\S+|"
    r"(?<!\w)(?:token|auth|cookie|signature|access_token|refresh_token)\s*=|"
    # An email candidate starts at its whitespace/@ token boundary. Without
    # it, each position retries the long local part: quadratic on labels
    # containing no @, or an incomplete address. Rejection stays identical.
    r"(?<![^\s@])[^\s@]+@[^\s@]+\.[^\s@]+)"
)


def _public_bibliographic_text(
    value: object, *, required: bool = False, limit: int = 8192,
) -> bool:
    """One scalar policy for public projection, durable C and structured metadata."""
    return (
        _text(value, required=required, limit=limit)
        and (value is None or (
            type(value) is str
            and _PRIVATE_BIBLIOGRAPHIC_PATTERN.search(value) is None
            and _PRIVATE_LOCATION.match(value.lstrip()) is None
        ))
    )


# Aggregate wire budget for admitted metadata, not a new C storage schema.
# Individual source text already has its own existing scalar limits.
_MAX_BIBLIOGRAPHY_BYTES = 32768
_PRIVATE_LOCATION = re.compile(r"(?i)^(?:[/\\\\]|~[/\\\\]|file:|[a-z]:[/\\\\])")


def _public_bibliographic_metadata(raw: object) -> dict | None:
    """Copy only existing explicit public fields, with an aggregate byte cap.

    No arbitrary provider extras, URLs, credentials, inferred ages, proof
    relabelling or rights. None means malformed/over-budget, not an empty
    inventory. Empty dict is a valid declaration with no such fields.
    This projection is reusable by a later versioned C writer, but cannot
    establish a source witness or authorize a write on its own.
    """
    if (type(raw) is not dict or len(raw) > 256
            or any(type(name) is not str for name in raw)):
        return None
    projected = {}
    used = 2  # JSON object delimiters
    for name in sorted(_BIBLIOGRAPHY):
        value = raw.get(name)
        if value is None:
            continue
        if type(value) is str:
            parts = (value,)
        elif type(value) in (list, tuple) and len(value) <= 32:
            parts = value
        else:
            return None
        # Explicit literary award labels have a tighter per-value bound.
        limit = 240 if name in ("awards", "prizes", "literary_prizes") else 8192
        for part in parts:
            if type(part) is not str or not _public_bibliographic_text(part, limit=limit):
                return None
        copied = value if type(value) is str else list(parts)
        # Count the actual bounded UTF-8 JSON, including escaping and keys.
        # No untrusted/unselected metadata is serialized or traversed.
        encoded = json.dumps({name: copied}, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        used += len(encoded) - 2 + bool(projected)
        if used > _MAX_BIBLIOGRAPHY_BYTES:
            return None
        projected[name] = copied
    return projected


# Descriptive labels, including explicitly declared literary awards. The
# award keys remain distinct from subject/genre in the shared local matcher.
_CLASSIFICATION_FIELDS = frozenset((
    "subjects", "subject", "genres", "genre", "tags", "keywords", "themes",
    "awards", "prizes", "literary_prizes",
))


@dataclass(frozen=True, slots=True)
class PublicClassification:
    """Immutable source-declared labels; no derived genre, age or relation."""

    entries: tuple[tuple[str, str | tuple[str, ...]], ...]

    def wire(self) -> dict:
        return {name: value if type(value) is str else list(value)
                for name, value in self.entries}


def decode_public_classification(raw: object) -> PublicClassification | None:
    if (type(raw) is not dict or not raw or len(raw) > 32
            or any(type(name) is not str for name in raw)
            or set(raw) - _CLASSIFICATION_FIELDS):
        return None
    projected = _public_bibliographic_metadata(raw)
    if projected is None or projected != raw:
        return None
    return PublicClassification(tuple(
        (name, value if type(value) is str else tuple(value))
        for name, value in sorted(projected.items())
    ))


def declared_public_classification(metadata: object) -> PublicClassification | None:
    if (type(metadata) is not dict or len(metadata) > 256
            or any(type(name) is not str for name in metadata)):
        return None
    selected = _public_bibliographic_metadata({
        name: metadata[name] for name in _CLASSIFICATION_FIELDS if name in metadata
    })
    return decode_public_classification(selected)



@dataclass(frozen=True, slots=True)
class PublicSeriesRelation:
    """One exact producer-declared relation; never a page facet or edition join."""

    label: str
    source: str
    source_field: str
    scope: str
    confidence: str
    freshness: str


def decode_public_series_relation(
    raw: object, *, provider_id: str, scenario: str | None = None,
) -> PublicSeriesRelation | None:
    """Closed proof: an exact linked relation or explicit record field."""
    names = {"label", "source", "source_field", "scope", "confidence", "freshness"}
    if (type(raw) is not dict or len(raw) > 16
            or any(type(name) is not str for name in raw)
            or set(raw) != names):
        return None
    if (not _public_bibliographic_text(raw["label"], required=True, limit=240)
            or type(provider_id) is not str or not provider_id
            or any(type(raw[name]) is not str or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", raw[name],
            ) is None for name in names - {"label"})
            or not raw["source"].startswith(provider_id + ".")
            or (scenario is not None and raw["scope"] != scenario)
            or raw["confidence"] not in ("linked_search_relation", "explicit_record_field")
            or raw["freshness"] != "search_response"):
        return None
    return PublicSeriesRelation(**raw)


def declared_public_series_relation(
    metadata: object, *, provider_id: str, scenario: str,
) -> PublicSeriesRelation | None:
    if (type(metadata) is not dict or len(metadata) > 256
            or any(type(name) is not str for name in metadata)):
        return None
    evidence = metadata.get("bibliography_evidence")
    values = metadata.get("series")
    if (type(evidence) is not dict or len(evidence) > 32
            or any(type(name) is not str for name in evidence)
            or type(values) is not list
            or len(values) != 1):
        return None
    proof = evidence.get("series")
    if (type(proof) is not dict or len(proof) > 16
            or any(type(name) is not str for name in proof)
            or set(proof) != {
                "source", "source_field", "scope", "confidence", "freshness",
            }):
        return None
    return decode_public_series_relation(
        {"label": values[0], **proof}, provider_id=provider_id, scenario=scenario,
    )


@dataclass(frozen=True, slots=True)
class PublicLanguageObservation:
    """A source's explicit language declaration, not a reconciled truth."""

    raw_tags: tuple[str, ...]
    languages: tuple[str, ...]
    source: str
    source_field: str
    scope: str
    confidence: str
    freshness: str

    def wire(self) -> dict:
        return {**asdict(self), "raw_tags": list(self.raw_tags),
                "languages": list(self.languages)}


def decode_public_language_observation(
    raw: object, *, provider_id: str, scenario: str | None = None,
) -> PublicLanguageObservation | None:
    from ..language import normalize_language_tag

    labels = {"raw_tags", "languages"}
    fields = {"source", "source_field", "scope", "confidence", "freshness"}
    if (type(raw) is not dict or len(raw) > 16
            or any(type(name) is not str for name in raw)
            or set(raw) != labels | fields):
        return None
    if (type(provider_id) is not str or not provider_id
            or any(type(raw[name]) is not str or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", raw[name],
            ) is None for name in fields)
            or not raw["source"].startswith(provider_id + ".")
            or (scenario is not None and raw["scope"] != scenario)
            or raw["confidence"] != "explicit_record_field"
            or raw["freshness"] != "search_response"):
        return None
    tags, values = raw["raw_tags"], raw["languages"]
    if (type(tags) is not list or not 1 <= len(tags) <= 16
            or type(values) is not list or not 1 <= len(values) <= 16
            or any(type(tag) is not str or len(tag) > 128
                   or re.fullmatch(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8}){0,8}", tag) is None
                   for tag in tags + values)):
        return None
    normalized = tuple(dict.fromkeys(normalize_language_tag(tag) for tag in tags))
    if None in normalized or normalized != tuple(values):
        return None
    return PublicLanguageObservation(
        tuple(tags), tuple(values), **{name: raw[name] for name in fields},
    )


def declared_public_language_observation(
    metadata: object, *, provider_id: str, scenario: str,
) -> PublicLanguageObservation | None:
    if (type(metadata) is not dict or len(metadata) > 256
            or any(type(name) is not str for name in metadata)):
        return None
    evidence = metadata.get("bibliography_evidence")
    if (type(evidence) is not dict or len(evidence) > 32
            or any(type(name) is not str for name in evidence)):
        return None
    proof = evidence.get("languages")
    if (type(proof) is not dict or len(proof) > 16
            or any(type(name) is not str for name in proof)
            or set(proof) != {
                "raw_tags", "source", "source_field", "scope", "confidence", "freshness",
            }):
        return None
    return decode_public_language_observation(
        {**proof, "languages": metadata.get("languages")},
        provider_id=provider_id, scenario=scenario,
    )


def _key(row: object) -> tuple | None:
    if type(row) is not SearchResult:
        return None
    values = tuple(getattr(row, name) for name in _KEY_FIELDS)
    if any(not _text(value, required=index != 6, limit=512)
           for index, value in enumerate(values)):
        return None
    if row.medium not in ("digital", "physical", "unknown"):
        return None
    return values


def _revision_marker(row: SearchResult) -> str | None:
    """Validated, LOCAL tie-break for *ambiguous* same-record declarations.

    A foreign private result may deliberately carry a different, private
    source revision from its single sanitized public bibliography claim.
    A revision is therefore NOT a mandatory match when exactly one public
    declaration exists. With multiple claims for the same record, however,
    only an exact and unique revision can disambiguate the public edition.
    The marker is never copied into the generic public DTO.
    """
    revision = row.revision_id
    if revision is None or revision == "":
        return ""
    if (type(revision) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", revision) is None):
        return None
    return revision


def project_public_search_result(
    response: ProviderSearchResponse, row: SearchResult, *,
    provider_id: str, credential_id: str,
) -> SearchResult | None:
    """Return the unique public projection of an exact response row or refuse.

    provider_id/credential_id are the expected *row source*, independently
    checked by the caller against the producing session (anonymous rows may
    have a distinct source). Never substitute the active profile's credential.
    Match the same response's exact row object, not a cached key from an old
    request. Revalidate the caller's identity/epoch lease after this function.

    v1 deliberately does not carry URLs, classifications or arbitrary extras.
    A producer's declaration attests public parsing, not legal rights, source
    ownership, complete coverage, or the truth of bibliographic information.
    """
    if (type(response) is not ProviderSearchResponse
            or type(response.results) is not list or len(response.results) > 10000
            or not any(item is row for item in response.results)
            or type(response.public_notices) is not tuple
            or len(response.public_notices) > 10000
            or not _text(provider_id, required=True, limit=512)
            or not _text(credential_id, required=True, limit=512)):
        return None
    key = _key(row)
    revision = _revision_marker(row) if key is not None else None
    if (key is None or revision is None
            or row.source_provider != provider_id
            or row.source_credential != credential_id):
        return None
    matches = []
    for notice in response.public_notices:
        if (type(notice) is not PublicSearchNotice
                or type(notice.schema_version) is not int
                or notice.schema_version != 1):
            return None
        candidate_key = _key(notice.result)
        if candidate_key is None or _revision_marker(notice.result) is None:
            return None
        if candidate_key == key:
            matches.append(notice.result)
    if len(matches) == 1:
        chosen = matches[0]
    elif len(matches) > 1:
        exact = [
            claim for claim in matches
            if _revision_marker(claim) == revision
        ]
        if len(exact) != 1:
            return None
        chosen = exact[0]
    else:
        return None
    return _project_public_notice(key, chosen)


def _project_public_notice(
    key: tuple, public: SearchResult,
) -> SearchResult | None:
    """Sanitize exactly one unique public declaration, without source I/O.

    Used by both direct one-row projection and the bounded batch index.
    """
    # Contradictory declarations are not silently laundered into public rows.
    if (type(public.owned) not in (bool, type(None)) or public.owned is True
            or type(public.downloadable) not in (bool, type(None))
            or public.downloadable is True
            or public.download_url is not None or public.source_account_id is not None
            or any(not _public_bibliographic_text(
                       getattr(public, name), required=name == "title",
                   ) for name in _TEXT_FIELDS)
            or type(public.metadata) is not dict or len(public.metadata) > 256):
        return None
    bibliography = _public_bibliographic_metadata(public.metadata)
    if bibliography is None:
        return None
    metadata = {"catalogue_access": "shared_read_only", **bibliography}
    relation = declared_public_series_relation(
        public.metadata, provider_id=public.source_provider, scenario=public.scenario,
    )
    proofs = {}
    if relation is not None:
        proof = asdict(relation)
        proof.pop("label")
        proofs["series"] = proof
    language = declared_public_language_observation(
        public.metadata, provider_id=public.source_provider, scenario=public.scenario,
    )
    if language is not None:
        proof = language.wire()
        proof.pop("languages")
        proofs["languages"] = proof
    if proofs:
        extension = {"bibliography_evidence": proofs}
        candidate = {**bibliography, **extension}
        if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )) <= _MAX_BIBLIOGRAPHY_BYTES:
            metadata.update(extension)
    # Construct afresh so future SearchResult fields cannot accidentally gain
    # public visibility through dataclasses.replace/asdict or raw metadata.
    return SearchResult(
        **dict(zip(_KEY_FIELDS, key)),
        **{name: getattr(public, name) for name in _TEXT_FIELDS},
        thumbnail_url=None, friendly_url=None,
        owned=False, downloadable=False, download_url=None, metadata=metadata,
    )


def project_public_search_results(
    response: ProviderSearchResponse,
) -> dict[int, SearchResult] | None:
    """Project one same-call public envelope in O(rows + public notices).

    The existing single-row API scans the response for an arbitrary caller.
    Federated Core already owns the entire response and must not repeat that
    scan once per result (10,000 rows previously caused quadratic work).
    This local index is keyed by *object identity* and is discarded after
    the same call. It is neither persisted nor a new documentary cache.
    Malformed declarations invalidate the entire envelope exactly as before;
    duplicate source coordinates are ambiguous, never silently selected.
    """
    if (type(response) is not ProviderSearchResponse
            or type(response.results) is not list
            or len(response.results) > 10000
            or type(response.public_notices) is not tuple
            or len(response.public_notices) > 10000):
        return None
    declarations: dict[tuple, list[SearchResult]] = {}
    by_revision: dict[tuple, dict[str, list[SearchResult]]] = {}
    for declaration in response.public_notices:
        if (type(declaration) is not PublicSearchNotice
                or type(declaration.schema_version) is not int
                or declaration.schema_version != 1):
            return None
        key = _key(declaration.result)
        revision = _revision_marker(declaration.result) if key is not None else None
        if key is None or revision is None:
            return None
        declarations.setdefault(key, []).append(declaration.result)
        by_revision.setdefault(key, {}).setdefault(
            revision, [],
        ).append(declaration.result)
    results: dict[int, SearchResult] = {}
    for row in response.results:
        if type(row) is not SearchResult:
            return None
        key = _key(row)
        revision = _revision_marker(row) if key is not None else None
        if key is None or revision is None:
            continue
        candidates = declarations.get(key, ())
        if len(candidates) == 1:
            chosen = candidates[0]
        elif len(candidates) > 1:
            # Same supplier ID can denote two public revisions. Use the
            # edition marker only to break that ambiguity, in O(1) per row.
            choices = by_revision[key].get(revision, ())
            if len(choices) != 1:
                continue
            chosen = choices[0]
        else:
            continue
        projected = _project_public_notice(key, chosen)
        if projected is not None:
            results[id(row)] = projected
    return results
