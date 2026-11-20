"""View-only source matching for already loaded grouped public C rows.

Keep complete source predicates independent. Never construct a new right,
edition relation, document lease, or persisted cache. Filter copies are
collapsed back to the original primary presentation after ranking.
"""
from __future__ import annotations

from .notice_c_public_search import PublicNoticeCHit
from .notice_c_cover_index import _MAX_SOURCES
from .notice_cover_repository import is_notice_cover_document

_TOKEN = object()
_MARKER = object()
_FIELD = "_core_notice_c_filter_sources"


def attach_filter_sources(row, hits):
    first = hits[0]
    if (2 <= len(hits) <= _MAX_SOURCES
            and all(type(hit) is PublicNoticeCHit
                    and hit.document == first.document and hit.epoch == first.epoch
                    for hit in hits)):
        row[_FIELD] = (_TOKEN, row["identity_id"], first.document, first.epoch, hits)


def _sources(row):
    if type(row) is not dict or row.get("source") != "notice_c_public":
        return None
    if _FIELD not in row:
        return None
    packet = row[_FIELD]
    meta = row.get("metadata")
    if (type(packet) is not tuple or len(packet) != 5 or packet[0] is not _TOKEN
            or packet[1] != row.get("identity_id")
            or packet[2] != row.get("_cover_document_ref")
            or not is_notice_cover_document(packet[2])
            or type(meta) is not dict
            or type(packet[3]) is not int
            or type(meta.get("notice_c_epoch")) is not int
            or packet[3] != meta["notice_c_epoch"]
            or type(packet[4]) is not tuple or not 2 <= len(packet[4]) <= _MAX_SOURCES):
        return ()
    hits = packet[4]
    if (any(type(hit) is not PublicNoticeCHit or hit.document != packet[2]
            or hit.epoch != packet[3] for hit in hits)
            or hits[0].provider_id != row.get("provider_id")
            or hits[0].record_id != row.get("item_id")):
        return ()
    return hits


def expand_filter_sources(rows, *, scope):
    """Expand only Core-projected groups, never loose alternative metadata."""
    from .notice_c_known_projection import _series_metadata

    expanded, originals = [], {}
    wanted = str(scope or "all").casefold()
    for row in rows:
        hits = _sources(row)
        if hits is None:
            expanded.append(row)
            continue
        marker = object()
        originals[marker] = row
        for hit in hits:
            if wanted in ("physical", "digital", "unknown") and hit.medium != wanted:
                continue
            expanded.append({
                _MARKER: marker,
                "source": "notice_c_public",
                "provider_id": hit.provider_id,
                "title": hit.title, "author": hit.author,
                "publisher": hit.publisher, "description": hit.description,
                "medium": hit.medium, "media_format": hit.media_format,
                "metadata": _series_metadata(hit),
            })
    return expanded, originals


def is_expanded_source(row):
    return _MARKER in row


def collapse_filter_sources(rows, originals):
    """Preserve best independent match ordering; no union or primary rewrite."""
    out, groups = [], {}
    for row in rows:
        marker = row.get(_MARKER)
        if marker not in originals:
            out.append(row)
            continue
        primary = groups.get(marker)
        if primary is None:
            primary = dict(originals[marker])
            primary["_notice_c_matched_providers"] = ()
            groups[marker] = primary
            out.append(primary)
        provider = row["provider_id"]
        if provider not in primary["_notice_c_matched_providers"]:
            primary["_notice_c_matched_providers"] += (provider,)
    return out

def matching_source_label(row):
    # This metadata is produced by collapse only; labels are descriptive.
    providers = row.get("_notice_c_matched_providers")
    if (type(providers) is not tuple or not providers
            or providers == (row.get("provider_id"),)):
        return ""
    if any(type(provider) is not str or not 1 <= len(provider) <= 64 for provider in providers):
        return ""
    suffix = f" (+{len(providers) - 3})" if len(providers) > 3 else ""
    return " · Correspondance : " + ", ".join(providers[:3]) + suffix
