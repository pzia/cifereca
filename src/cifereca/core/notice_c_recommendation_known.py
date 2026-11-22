"""Read-only Recommendation candidates from the existing persisted C cursor.

No action-capable source credentials, supplier session, URL or live producer
witness are reconstructed. The worker-created receipt belongs to the exact
projected object; only the UI host can exchange it for a current-view lease.
"""
from __future__ import annotations

from dataclasses import asdict, fields
from hashlib import sha256
from typing import Any

from ..models import SearchResult
from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import _MAX_SOURCES, is_canonical_cover_document
from .notice_c_public_search import PublicNoticeCHit, search_public_notice_c
from .notice_c_known_projection import _series_metadata, _source_alternative
from .public_search_projection import (
    PublicClassification, PublicLanguageObservation, PublicSeriesRelation,
    decode_public_classification, decode_public_language_observation,
    decode_public_series_relation,
)
from .notice_c_public_age import (
    PublicAgeObservation, PublicAgeRange, decode_public_age_observation,
)

_TOKEN = object()
_DTO_FIELDS = tuple(field.name for field in fields(SearchResult))


def _source_receipt(hit):
    """Seal unprojected native edition and observation coordinates at admission."""
    # Source alternatives expose title/author/series but do not project
    # their docbase, medium, format or N2 freshness into the visible DTO.
    # Seal those fields too: a frozen PublicNoticeCHit can be rewritten via
    # object.__setattr__ without changing the displayed alternative summary.
    return (id(hit), hit.edition_id, hit.observed_at, hit.last_seen_at,
            hit.docbase, hit.medium, hit.media_format, hit.cover_state, hit.stale)



def _native_canonical(document):
    # Frozen CoverDocumentScope can still have foreign attributes injected.
    # Refuse them before is_canonical_cover_document compares fields.
    return (type(document) is CoverDocumentScope
            and all(type(getattr(document, name)) is str
                    and len(getattr(document, name)) <= 512
                    and getattr(document, name).isprintable()
                    for name in ("kind", "source_key", "provider_id",
                                 "credential_id", "loan_id", "artifact_id",
                                 "placement_id"))
            and (document.identity_id is None
                 or type(document.identity_id) is str)
            and (document.account_id is None
                 or type(document.account_id) is str)
            and is_canonical_cover_document(document))


def _native_hit(hit, binding):
    # A frozen PublicNoticeCHit or nested public evidence can be altered
    # after the original Core receipt was issued. Refuse all malformed
    # members before generating a new source summary or dereferencing ages.
    if (type(hit) is not PublicNoticeCHit
            or not _native_canonical(hit.document)
            or type(hit.epoch) is not int
            or type(hit.observed_at) is not int
            or not 0 <= hit.observed_at < 2**63
            or type(hit.last_seen_at) is not int
            or not hit.observed_at <= hit.last_seen_at < 2**63
            or type(hit.stale) is not bool
            or type(hit.cover_state) is not str
            or hit.cover_state not in ("available", "missing", "pending")
            or type(hit.edition_id) is not str
            or len(hit.edition_id) > 512
            or (hit.document, hit.epoch) != binding
            or any(value is not None and (
                type(value) is not str or len(value) > 8192)
                   for value in (
                       hit.provider_id, hit.record_id, hit.docbase, hit.medium,
                       hit.source_revision, hit.title, hit.author, hit.publisher,
                       hit.published_at, hit.media_format, hit.description,
                   ))):
        return False
    series = hit.series_relation
    if series is not None:
        if (type(series) is not PublicSeriesRelation
                or any(type(getattr(series, field.name)) is not str
                       for field in fields(PublicSeriesRelation))
                or decode_public_series_relation(
                    asdict(series), provider_id=hit.provider_id,
                ) != series):
            return False
    language = hit.language_observation
    if language is not None:
        if (type(language) is not PublicLanguageObservation
                or any(type(getattr(language, name)) is not tuple
                       or len(getattr(language, name)) > 16
                       or any(type(value) is not str for value in getattr(language, name))
                       for name in ("raw_tags", "languages"))
                or any(type(getattr(language, name)) is not str for name in
                       ("source", "source_field", "scope", "confidence", "freshness"))
                or decode_public_language_observation(
                    language.wire(), provider_id=hit.provider_id,
                ) != language):
            return False
    classification = hit.classification
    if classification is not None:
        if (type(classification) is not PublicClassification
                or type(classification.entries) is not tuple
                or len(classification.entries) > 16):
            return False
        for pair in classification.entries:
            if (type(pair) is not tuple or len(pair) != 2
                    or type(pair[0]) is not str
                    or not (type(pair[1]) is str
                            or (type(pair[1]) is tuple
                                and len(pair[1]) <= 32
                                and all(type(value) is str for value in pair[1])))):
                return False
        if decode_public_classification(classification.wire()) != classification:
            return False
    age = hit.age_observation
    if age is not None:
        if (type(age) is not PublicAgeObservation
                or any(type(getattr(age, name)) is not str for name in
                       ("provider_id", "record_id", "docbase", "medium", "source_field"))
                or (age.source_revision is not None
                    and type(age.source_revision) is not str)
                or type(age.ranges) is not tuple or not 1 <= len(age.ranges) <= 16):
            return False
        for age_range in age.ranges:
            if (type(age_range) is not PublicAgeRange
                    or any(value is not None and type(value) is not int
                           for value in (age_range.age_min, age_range.age_max))
                    or type(age_range.age_kind) is not str
                    or type(age_range.source_label) is not str
                    or len(age_range.source_label) > 120):
                return False
        if decode_public_age_observation(
            age.wire(), provider_id=hit.provider_id, record_id=hit.record_id,
            docbase=hit.docbase, medium=hit.medium,
            source_revision=hit.source_revision,
        ) != age:
            return False
    return True


def _same_native_snapshot(left, right, depth=0):
    # The original DTO snapshot is bounded and contains only tuple/str/int/
    # bool/None. Compare this known native tree against a possibly modified
    # receipt without ever invoking foreign __eq__ on its inner members.
    if depth > 24 or type(left) is not type(right):
        return False
    if type(left) is tuple:
        return (len(left) == len(right)
                and all(_same_native_snapshot(a, b, depth + 1)
                        for a, b in zip(left, right)))
    if type(left) in (str, int, bool, type(None)):
        return left == right
    return False


def _snapshot(item):
    """Bounded native immutable DTO receipt; no deepcopy, repr or coercion."""
    # The canonical reader bounds a document to 32 sources. Each
    # admitted alternative needs the same bounded receipt space as one
    # source; all native-container/depth/string checks remain in force.
    metadata = item.metadata
    # Inspect only native keys before dictionary lookup: an arbitrary key
    # with a colliding hash could otherwise run its __eq__ during .get().
    if (type(metadata) is not dict or len(metadata) > 64
            or any(type(key) is not str or len(key) > 160 for key in metadata)):
        raise ValueError("known_c_snapshot_non_native")
    alternatives = metadata.get("notice_c_source_alternatives")
    count = len(alternatives) if type(alternatives) is list else 0
    if count >= _MAX_SOURCES:
        raise ValueError("known_c_snapshot_budget")
    remaining = 512 * (1 + count)
    characters = 65536 + (131072 if count else 0)

    def freeze(value, depth=0):
        nonlocal remaining, characters
        remaining -= 1
        # An alternative's structured age is deeper than a language tag:
        # DTO -> metadata -> alternatives -> source -> evidence -> youth_age
        # -> ranges -> range -> scalar. Keep node/string budgets unchanged.
        if remaining < 0 or depth > (8 if count else 6):
            raise ValueError("known_c_snapshot_budget")
        kind = type(value)
        if kind is str:
            characters -= len(value)
            if len(value) > 8192 or characters < 0:
                raise ValueError("known_c_snapshot_budget")
            return ("str", value)
        if kind is bool or kind is int or value is None:
            return (kind.__name__, value)
        if kind is list and len(value) <= 32:
            return ("list", tuple(freeze(child, depth + 1) for child in value))
        if (kind is dict and len(value) <= 64
                and all(type(key) is str and len(key) <= 160 for key in value)):
            characters -= sum(len(key) for key in value)
            if characters < 0:
                raise ValueError("known_c_snapshot_budget")
            return ("dict", tuple((key, freeze(value[key], depth + 1))
                                  for key in sorted(value)))
        raise ValueError("known_c_snapshot_non_native")

    return freeze({name: getattr(item, name) for name in _DTO_FIELDS})


def known_public_notice_candidates(
    storage: Any, query: str, *, scope: str = "all",
    provider_ids: tuple[str, ...] = (),
) -> list[SearchResult]:
    page = search_public_notice_c(
        storage, query, scope=scope, provider_ids=provider_ids,
        group_by_document=True,
    )
    rows = []
    sources_by_document = {}
    for source in page.matching_sources:
        sources_by_document.setdefault(source.document, []).append(source)
    seen = set()
    for hit in page.hits:
        # Use only the canonical C preferred by the existing index. Do not
        # manufacture another canonical relation from a ranking cluster.
        if not is_canonical_cover_document(hit.document) or hit.document in seen:
            continue
        seen.add(hit.document)
        row = SearchResult(
            id="known-c:" + sha256(hit.document.source_key.encode()).hexdigest(),
            record_id=hit.record_id, docbase=hit.docbase, medium=hit.medium,
            title=hit.title, author=hit.author, type="book",
            format=hit.media_format, publisher=hit.publisher, published_at=hit.published_at,
            description=hit.description, thumbnail_url=None, friendly_url=None,
            scenario="notice_c_public", source_provider=hit.provider_id,
            source_credential="", source_collection="notice_c_public",
            revision_id=hit.source_revision, owned=None, downloadable=None,
            # Share Search C's documentary status vocabulary. This is a
            # partial *known-source* page, never a complete supplier query
            # or an indication of purchase/borrowing rights.
            metadata={"notice_c_known": True, "notice_c_stale": hit.stale,
                      "notice_c_query_status": page.status,
                      "notice_c_cover_state": hit.cover_state,
                      "notice_c_epoch": hit.epoch,
                      **_series_metadata(hit)},
        )
        alternative_hits = tuple(
            source for source in sources_by_document.get(hit.document, ())
            if source is not hit
        )
        alternatives = [_source_alternative(source) for source in alternative_hits]
        if alternatives:
            row.metadata["notice_c_source_alternatives"] = alternatives
        # Exact object and full DTO snapshot, not a serialized proof. Sources
        # with missing covers are still descriptive candidates.
        try:
            snapshot = _snapshot(row)
        except (ValueError, TypeError):
            # One over-budget canonical group must not abort the entire
            # partial page or weaken the receipt by dropping source facts.
            # Other independently admitted documents remain usable.
            continue
        row._core_known_public_c_projection = (
            _TOKEN, id(row), hit.document, hit.epoch, snapshot, alternative_hits, hit,
        )
        # These exact source coordinates are not all rendered as DTO fields.
        # Freezing the dataclass does not prevent object.__setattr__ mutation.
        row._core_known_public_c_primary_source_receipt = _source_receipt(hit)
        row._core_known_public_c_alternative_source_receipts = tuple(
            _source_receipt(source) for source in alternative_hits
        )
        rows.append(row)
    return rows


def known_public_c_binding_facts(item: object):
    if type(item) is not SearchResult:
        return None
    proof = getattr(item, "_core_known_public_c_projection", None)
    if (type(proof) is not tuple or len(proof) != 7
            or proof[0] is not _TOKEN or proof[1] != id(item)
            or not _native_canonical(proof[2])
            or type(proof[3]) is not int or not 1 <= proof[3] < 2**63
            or type(proof[4]) is not tuple):
        return None
    # Compare only bounded native public fields. The opaque packet's
    # nested snapshot can be replaced independently of the SearchResult.
    # Never execute equality from a foreign object inside that packet.
    try:
        if not _same_native_snapshot(_snapshot(item), proof[4]):
            return None
    except (ValueError, TypeError):
        return None
    if (item.source_credential != "" or item.source_account_id is not None
            or item.source_collection != "notice_c_public"
            or item.downloadable is not None or item.owned is not None
            or item.thumbnail_url is not None or item.download_url is not None
            or item.friendly_url is not None):
        return None
    return proof[2], proof[3]


def known_public_c_alternative_hits(item: object):
    """Return exact retained sources only after the native DTO receipt check."""
    binding = known_public_c_binding_facts(item)
    if binding is None:
        return None
    hits = item._core_known_public_c_projection[5]
    alternatives = item.metadata.get("notice_c_source_alternatives", [])
    if (type(hits) is not tuple or len(hits) >= _MAX_SOURCES
            or type(alternatives) is not list or len(hits) != len(alternatives)
            or any(not _native_hit(hit, binding) for hit in hits)
            or getattr(item, "_core_known_public_c_alternative_source_receipts", None)
               != tuple(_source_receipt(hit) for hit in hits)
            or [_source_alternative(hit) for hit in hits] != alternatives):
        return None
    return hits


def known_public_c_primary_hit(item: object):
    """Exact immutable primary C source, only on the original bounded DTO.

    Never infer an age from user-supplied metadata, title, ISBN or ranking.
    This private Core fact cannot be replayed by a copied provider result.
    """
    binding = known_public_c_binding_facts(item)
    if binding is None:
        return None
    hit = item._core_known_public_c_projection[6]
    if (not _native_hit(hit, binding)
            or getattr(item, "_core_known_public_c_primary_source_receipt", None)
               != _source_receipt(hit)
            or (hit.provider_id, hit.record_id, hit.docbase, hit.medium,
                hit.source_revision, hit.title, hit.author, hit.publisher,
                hit.published_at, hit.media_format, hit.description) != (
                    item.source_provider, item.record_id, item.docbase, item.medium,
                    item.revision_id, item.title, item.author, item.publisher,
                    item.published_at, item.format, item.description)):
        return None
    # The SearchResult DTO is sealed by _snapshot, but the private
    # PublicNoticeCHit referenced by the receipt is a frozen dataclass that
    # can still be mutated through object.__setattr__. A valid *different*
    # classification, series or age must not silently acquire this edition's
    # primary provenance without changing any displayed scalar field.
    projected = _series_metadata(hit)
    reserved = {
        "notice_c_known", "notice_c_stale", "notice_c_query_status",
        "notice_c_cover_state", "notice_c_epoch",
        "notice_c_source_alternatives",
    }
    visible_facts = {key: value for key, value in item.metadata.items()
                     if key not in reserved}
    if (item.metadata.get("notice_c_stale") is not hit.stale
            or item.metadata.get("notice_c_cover_state") != hit.cover_state
            or visible_facts != projected):
        return None
    return hit
