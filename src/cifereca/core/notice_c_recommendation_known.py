"""Read-only Recommendation candidates from the existing persisted C cursor.

No action-capable source credentials, supplier session, URL or live producer
witness are reconstructed. The worker-created receipt belongs to the exact
projected object; only the UI host can exchange it for a current-view lease.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from typing import Any

from ..models import SearchResult
from .notice_c_cover_index import is_canonical_cover_document
from .notice_c_public_search import search_public_notice_c

_TOKEN = object()


def known_public_notice_candidates(
    storage: Any, query: str, *, scope: str = "all",
    provider_ids: tuple[str, ...] = (),
) -> list[SearchResult]:
    page = search_public_notice_c(
        storage, query, scope=scope, provider_ids=provider_ids,
        group_by_document=True,
    )
    rows = []
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
            metadata={"notice_c_known": True, "notice_c_stale": hit.stale},
        )
        # Exact object and full DTO snapshot, not a serialized proof. Sources
        # with missing covers are still descriptive candidates.
        row._core_known_public_c_projection = (
            _TOKEN, id(row), hit.document, hit.epoch, asdict(row),
        )
        rows.append(row)
    return rows


def known_public_c_binding_facts(item: object):
    if type(item) is not SearchResult:
        return None
    proof = getattr(item, "_core_known_public_c_projection", None)
    if (type(proof) is not tuple or len(proof) != 5
            or proof[0] is not _TOKEN or proof[1] != id(item)
            or not is_canonical_cover_document(proof[2])
            or type(proof[3]) is not int or not 1 <= proof[3] < 2**63
            or item.source_credential != "" or item.source_account_id is not None
            or item.source_collection != "notice_c_public"
            or item.downloadable is not None or item.owned is not None
            or item.thumbnail_url is not None or item.download_url is not None
            or item.friendly_url is not None):
        return None
    # Compare only bounded native public fields. No callbacks, repr or
    # provider-supplied coercion on GTK.
    original = proof[4]
    for key, expected in original.items():
        actual = getattr(item, key, None)
        if type(actual) is not type(expected):
            return None
        if key == "metadata":
            if (type(actual) is not dict or len(actual) != 2
                    or any(type(k) is not str or type(v) is not bool
                           for k, v in actual.items()) or actual != expected):
                return None
        elif actual != expected:
            return None
    return proof[2], proof[3]
