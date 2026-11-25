"""View-only source matching for already loaded grouped public C rows.

Keep complete source predicates independent. Never construct a new right,
edition relation, document lease, or persisted cache. Filter copies are
collapsed back to the original primary presentation after ranking.
"""
from __future__ import annotations

from .notice_c_public_search import PublicNoticeCHit
from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import _MAX_SOURCES
from .notice_cover_repository import is_notice_cover_document

_TOKEN = object()
_MARKER = object()
_MATCH_HIT = object()
_MATCH_FIELD = "_core_notice_c_match_source"
_FIELD = "_core_notice_c_filter_sources"


def _native_group_document(document):
    # Core's frozen scope can be mutated after issuance. Revalidate scalar
    # members before dataclass equality, then re-run the constructor's
    # public/private invariants before repository source eligibility.
    if type(document) is not CoverDocumentScope:
        return False
    for name in ("kind", "source_key", "provider_id", "credential_id",
                 "loan_id", "artifact_id", "placement_id"):
        value = getattr(document, name)
        if type(value) is not str or len(value) > 512 or not value.isprintable():
            return False
    for name in ("identity_id", "account_id"):
        value = getattr(document, name)
        if (value is not None and (type(value) is not str
                                   or len(value) > 512
                                   or not value.isprintable())):
            return False
    try:
        document.__post_init__()
    except (TypeError, ValueError):
        return False
    return is_notice_cover_document(document)


def _source_scalar_snapshot(hit):
    """Seal every alternative's source scalars and native DocumentRef at admission.

    Source evidence (classification/age/language) is separately frozen.
    A frozen hit can still be mutated in place by a view or callback; the
    grouped filter must never attribute that new title/provider/format to C.
    """
    from .notice_c_known_projection import (
        _PRIMARY_HIT_SEAL_NAMES, _PRIMARY_DOCUMENT_SEAL_NAMES,
    )

    if type(hit) is not PublicNoticeCHit or not _native_group_document(hit.document):
        return None
    values = tuple(getattr(hit, name) for name in _PRIMARY_HIT_SEAL_NAMES)
    document = tuple(getattr(hit.document, name) for name in _PRIMARY_DOCUMENT_SEAL_NAMES)
    if (any(type(value) not in (str, int, bool, type(None))
            or (type(value) is str and len(value) > 8192) for value in values)
            or any(type(value) not in (str, type(None))
                   or (type(value) is str and len(value) > 512) for value in document)):
        return None
    return values, document


def attach_filter_sources(row, hits):
    # Construct the opaque packet only from an exact, internally consistent
    # Core group. Avoid indexing an empty tuple or invoking foreign equality
    # on a hit whose frozen fields have been replaced after the source read.
    if (type(row) is not dict or type(hits) is not tuple
            or not 2 <= len(hits) <= _MAX_SOURCES
            or type(row.get("identity_id")) is not str):
        return
    first = hits[0]
    if (type(first) is not PublicNoticeCHit
            or not _native_group_document(first.document)
            or type(first.epoch) is not int
            or type(first.provider_id) is not str
            or type(first.record_id) is not str):
        return
    if all(type(hit) is PublicNoticeCHit
           and _native_group_document(hit.document)
           and type(hit.epoch) is int
           and type(hit.provider_id) is str
           and type(hit.record_id) is str
           and hit.document == first.document and hit.epoch == first.epoch
           for hit in hits):
        from .notice_c_known_projection import _source_evidence_snapshot
        proofs = tuple((
            _source_evidence_snapshot(hit), _source_scalar_snapshot(hit),
        ) for hit in hits)
        if any(evidence is None or scalars is None for evidence, scalars in proofs):
            return
        row[_FIELD] = (
            _TOKEN, row["identity_id"], first.document, first.epoch, hits, proofs,
        )


def _sources(row):
    if (type(row) is not dict or type(row.get("source")) is not str
            or row["source"] != "notice_c_public"):
        return None
    if _FIELD not in row:
        return None
    packet = row[_FIELD]
    meta = row.get("metadata")
    identity = row.get("identity_id")
    document = row.get("_cover_document_ref")
    if (type(packet) is not tuple or len(packet) != 6 or packet[0] is not _TOKEN
            or type(identity) is not str or type(packet[1]) is not str
            or packet[1] != identity
            or not _native_group_document(packet[2])
            or not _native_group_document(document)
            or packet[2] != document
            or type(meta) is not dict
            or type(packet[3]) is not int
            or type(meta.get("notice_c_epoch")) is not int
            or packet[3] != meta["notice_c_epoch"]
            or type(packet[4]) is not tuple or not 2 <= len(packet[4]) <= _MAX_SOURCES
            or type(packet[5]) is not tuple or len(packet[5]) != len(packet[4])):
        return ()
    hits = packet[4]
    from .notice_c_known_projection import (
        _source_evidence_snapshot, _same_source_evidence_snapshot,
    )
    if any(
        type(proof) is not tuple or len(proof) != 2
        or not _same_source_evidence_snapshot(
            _source_evidence_snapshot(hit), proof[0])
        or not _same_source_evidence_snapshot(
            _source_scalar_snapshot(hit), proof[1])
        for hit, proof in zip(hits, packet[5])
    ):
        return ()
    if (any(type(hit) is not PublicNoticeCHit
            or not _native_group_document(hit.document)
            or type(hit.epoch) is not int
            or type(hit.provider_id) is not str
            or type(hit.record_id) is not str
            or hit.document != packet[2] or hit.epoch != packet[3]
            for hit in hits)
            or type(row.get("provider_id")) is not str
            or type(row.get("item_id")) is not str
            or hits[0].provider_id != row["provider_id"]
            or hits[0].record_id != row["item_id"]):
        return ()
    # A group is bound to the same admitted primary observation, not
    # merely to an equal-looking provider/record pair. Do not allow a
    # replaced presentation or primary receipt to authorize alternatives.
    from .notice_c_known_projection import known_public_primary_facts
    if known_public_primary_facts(row) is not hits[0]:
        return ()
    return hits


def _filter_source_row(hit, *, marker=None):
    """The shared, purely descriptive projection of one retained source."""
    from .notice_c_known_projection import _series_metadata

    row = {
        "source": "notice_c_public",
        "provider_id": hit.provider_id,
        "item_id": hit.record_id,
        "title": hit.title, "author": hit.author,
        "publisher": hit.publisher, "description": hit.description,
        "medium": hit.medium, "media_format": hit.media_format,
        "metadata": _series_metadata(hit),
    }
    if marker is not None:
        row[_MARKER] = marker
        row[_MATCH_HIT] = hit
    return row


def matches_known_public_c_filters(item, *, query, field, scope, providers,
                                   content_types, languages):
    """Filter a known recommendation with the same per-source Search rules.

    None means an ordinary candidate; False means no admissible match.
    Only the native, original Core receipt admits alternative sources.
    No storage access, provider action, rank change or new receipt.
    """
    from ..models import SearchResult
    from .notice_c_recommendation_known import (
        known_public_c_primary_hit, known_public_c_alternative_hits,
    )
    from .document_filter import filter_loaded_with_documentary_proofs

    if (type(item) is not SearchResult or type(item.scenario) is not str
            or item.scenario != "notice_c_public"):
        return None
    if (type(query) is not str or len(query) > 2048 or not query.isprintable()
            or type(field) is not str or field not in (
                "all", "title", "author", "isbn", "subject", "series", "collection", "language")
            or type(scope) is not str or scope not in (
                "all", "digital", "physical", "unknown")):
        return False
    for values in (providers, content_types, languages):
        if (type(values) is not tuple or len(values) > 128
                or any(type(value) is not str or not value or len(value) > 120
                       or value != value.strip() or not value.isprintable()
                       for value in values)):
            return False
    primary = known_public_c_primary_hit(item)
    alternatives = known_public_c_alternative_hits(item)
    if primary is None:
        return False
    # A revoked or malformed alternative packet cannot grant a match, but
    # must not remove the independently verified primary. Search keeps the
    # same primary-only fallback. A mutated/copied DTO still has no primary.
    if alternatives is None:
        alternatives = ()
    # An expanded group has exactly the same medium prefilter and typed
    # classification handling as the Search list. Do not mix metadata
    # from one source with the selected catalogue of another.
    if alternatives:
        marker = object()
        rows = [
            _filter_source_row(hit, marker=marker)
            for hit in (primary, *alternatives)
            if scope == "all" or hit.medium == scope
        ]
    else:
        rows = [_filter_source_row(primary)]
    return bool(filter_loaded_with_documentary_proofs(
        rows, query, field=field, scope=scope, providers=providers,
        content_types=content_types, languages=languages,
    ))


def expand_filter_sources(rows, *, scope):
    """Expand only Core-projected groups, never loose alternative metadata."""

    expanded, originals = [], {}
    wanted = str(scope or "all").casefold()
    for row in rows:
        hits = _sources(row)
        if not hits:
            # An invalidated multi-source packet is NOT evidence that the
            # primary C row vanished. Keep ordinary primary filtering, but
            # never expand untrusted alternatives or grant their matches.
            # _sources returns () for a stale/mutated group and None for an
            # ungrouped row.
            expanded.append(row)
            continue
        marker = object()
        originals[marker] = row
        for hit in hits:
            if wanted in ("physical", "digital", "unknown") and hit.medium != wanted:
                continue
            expanded.append(_filter_source_row(hit, marker=marker))
    return expanded, originals


def is_expanded_source(row):
    return type(row) is dict and type(row.get(_MARKER)) is object


def collapse_filter_sources(rows, originals):
    """Preserve best independent match ordering; no union or primary rewrite."""
    out, groups = [], {}
    for row in rows:
        marker = row.get(_MARKER)
        # Only our object() sentinel can be a group key. A plugin-facing
        # row must never execute an arbitrary __hash__/__eq__ in this dict.
        if type(marker) is not object or marker not in originals:
            out.append(row)
            continue
        primary = groups.get(marker)
        if primary is None:
            primary = dict(originals[marker])
            primary["_notice_c_matched_providers"] = ()
            primary.pop(_MATCH_FIELD, None)
            hit = row.get(_MATCH_HIT)
            if type(hit) is PublicNoticeCHit:
                # First independently filtered source is the highest ranked
                # match. Preserve its receipt, never union the other fields.
                primary[_MATCH_FIELD] = (_TOKEN, hit)
            groups[marker] = primary
            out.append(primary)
        provider = row.get("provider_id")
        if (type(provider) is str and 0 < len(provider) <= 64
                and provider not in primary["_notice_c_matched_providers"]):
            primary["_notice_c_matched_providers"] += (provider,)
    return out

def matching_source_label(row):
    # This metadata is produced by collapse only; labels are descriptive.
    providers = row.get("_notice_c_matched_providers")
    primary = row.get("provider_id")
    if (type(providers) is not tuple or not providers
            or any(type(provider) is not str or not 1 <= len(provider) <= 64
                   for provider in providers)
            or type(primary) is not str or providers == (primary,)):
        return ""
    suffix = f" (+{len(providers) - 3})" if len(providers) > 3 else ""
    return " · Correspondance : " + ", ".join(providers[:3]) + suffix


def matching_source_reason(row, query, *, field="all"):
    """Explain the exact best filtered C source; no I/O or new authority."""
    from .notice_c_known_projection import known_public_primary_facts
    from .search import local_match_explanation

    if type(query) is not str or not query.strip() or field != "all":
        return ""
    hit = known_public_primary_facts(row)
    if hit is None:
        return ""
    if _MATCH_FIELD in row:
        receipt = row[_MATCH_FIELD]
        if (type(receipt) is not tuple or len(receipt) != 2
                or receipt[0] is not _TOKEN
                or not any(candidate is receipt[1] for candidate in (_sources(row) or ()))):
            return ""
        hit = receipt[1]
    elif _FIELD in row and _sources(row):
        # A genuine group must have been filtered before explaining it.
        return ""
    return local_match_explanation(_filter_source_row(hit), query, field=field)
