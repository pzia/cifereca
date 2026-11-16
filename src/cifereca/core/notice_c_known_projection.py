"""#6366/#2385: read-only C-first results for the existing async known Search.

A PUBLIC Notice C source has a durable DocumentRef and descriptive text,
but NO provider session, identity owner, loan or acquisition. Never merge C
with another notice by title/ISBN/image/legacy record ID. Keep an independently
possessed local book, and suppress only the exact duplicated anonymous public
mirror source when its full edition coordinates agree.

This pure projection performs no SQLite/GTK/network/file I/O; its caller owns
the asynchronously scheduled query and the identity/generation lease.
"""
from __future__ import annotations

from typing import Any

from .notice_c_public_search import PublicNoticeCPage, PublicNoticeCHit
from .notice_cover_repository import is_notice_cover_document

_MAX = 50


def _same_public_mirror_source(
    row: dict[str, Any], hit: PublicNoticeCHit, *, identity_id: str,
) -> bool:
    """The legacy mirror may be redundant, but never an action proof."""
    if (type(row) is not dict
            or row.get("source") != "known_catalog"
            or row.get("identity_id") != identity_id
            or row.get("local") is True
            or row.get("placement_id")
            or row.get("state") in (
                "local", "remote_owned", "remote_loan_active",
                "remote_loan_realized",
            )
            or row.get("provider_id") != hit.provider_id
            or row.get("item_id") != hit.record_id):
        return False
    meta = row.get("metadata")
    if (type(meta) is not dict
            or meta.get("catalogue_scope_version") not in (2, 3)
            or meta.get("source_credential") != "__public__"
            or meta.get("source_collection") != "public_catalog"
            or meta.get("docbase") != hit.docbase
            or meta.get("medium") != hit.medium
            or meta.get("source_account_id") not in (None, "")):
        return False
    if meta.get("catalogue_scope_version") == 2:
        return hit.source_revision is None
    return meta.get("source_revision") == hit.source_revision


def merge_public_notice_c_known_rows(
    known: list[dict[str, Any]], page: PublicNoticeCPage,
    *, identity_id: str,
) -> list[dict[str, Any]]:
    """Present known C before the mirror; source C never conveys rights.

    The C query cursor limits itself to 50 known observations, so this first
    stage has no claim to full query coverage. An empty page is unknown, not
    evidence that a provider has zero documents.
    """
    if (type(known) is not list or type(page) is not PublicNoticeCPage
            or type(identity_id) is not str or not identity_id
            or len(page.hits) > _MAX):
        raise ValueError("invalid_known_public_c_projection")

    rows: list[dict[str, Any]] = []
    admitted: list[PublicNoticeCHit] = []
    seen: set[str] = set()
    for hit in page.hits:
        if (type(hit) is not PublicNoticeCHit
                or hit.document.kind != "public_catalogue"
                or hit.document.identity_id is not None
                or not is_notice_cover_document(hit.document)
                or hit.document.source_key in seen):
            continue
        seen.add(hit.document.source_key)
        admitted.append(hit)
        rows.append({
            "key": "c-public:" + hit.document.source_key,
            "source": "notice_c_public",
            "identity_id": identity_id,
            "provider_id": hit.provider_id,
            "item_id": hit.record_id,
            "title": hit.title,
            "author": hit.author,
            "media_format": hit.media_format,
            "medium": hit.medium,
            "state": "remote_catalog",
            "local": False,
            "local_path": None,
            "thumbnail_url": None,  # N2 reads C; never raw URL fallback.
            "metadata": {
                "notice_c_public": True,
                "notice_c_stale": hit.stale,
                "notice_c_cover_state": hit.cover_state,
                "notice_c_epoch": hit.epoch,
                "notice_c_query_status": page.status,
                "publisher": hit.publisher,
                "description": hit.description,
            },
            "_cover_document_ref": hit.document,
        })
    for row in known:
        # Do not replace local placements, verified rights, private sources,
        # different revisions or medium with public descriptive metadata.
        if not any(_same_public_mirror_source(row, hit, identity_id=identity_id)
                   for hit in admitted):
            rows.append(row)
    return rows


__all__ = ["merge_public_notice_c_known_rows"]
