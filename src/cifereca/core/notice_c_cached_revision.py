"""Ephemeral read-through proof, issued only after a persisted C lookup."""
from __future__ import annotations

from ..models import SearchResult
from .notice_c_live_revision import persisted_live_c_revision, stamp_persisted_c_revision

_TOKEN = object()


def _coordinates(row):
    return (row.source_provider, row.source_credential, row.source_collection,
            row.source_account_id, row.record_id, row.docbase, row.medium,
            row.revision_id, row.title, row.author, row.thumbnail_url,
            row.owned, row.downloadable, row.download_url,
            row.metadata.get("search_provenance") if type(row.metadata) is dict else None)


def stamp_cached_c_revision(row, snapshot, *, identity_id):
    stamp_persisted_c_revision(row, snapshot)
    row._core_cached_c_receipt = (
        _TOKEN, id(row), identity_id, _coordinates(row), snapshot.document,
    )


def matching_cached_public_canonical_ref(row, *, identity_id):
    if type(row) is not SearchResult:
        return None
    proof = getattr(row, "_core_cached_c_receipt", None)
    if (type(proof) is not tuple or len(proof) != 5
            or proof[0] is not _TOKEN or proof[1] != id(row)
            or proof[2] != identity_id or proof[3] != _coordinates(row)):
        return None
    if persisted_live_c_revision(row, proof[4]) is None:
        return None
    return proof[4]
