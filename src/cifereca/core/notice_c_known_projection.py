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


def matching_live_public_canonical_ref(row: object, *, identity_id: str):
    """Match one same-call live result to its Core-attested canonical C ref.

    A mutable provider-facing SearchResult may carry arbitrary attributes.
    Re-derive both source and edition coordinates under the private Core
    witness; a title/URL alone cannot retire a C-first row from the view.
    Pure projection: no DB, GTK, entitlement, image IO or provider calls.
    """
    from ..models import SearchResult
    from .search_orchestration import _verified_public_cover_witness
    from .notice_search_cover_producer import admit_public_search_cover
    from .notice_cover_projection import notice_cover_document_ref
    from .notice_c_cover_index import (
        canonical_cover_document_ref, is_canonical_cover_document,
    )

    if (type(row) is not SearchResult
            or type(identity_id) is not str or not identity_id
            or not _verified_public_cover_witness(row)):
        return None
    source = getattr(row, "_core_notice_cover_document_ref", None)
    canonical = getattr(row, "_core_notice_canonical_cover_document_ref", None)
    if not is_canonical_cover_document(canonical):
        return None
    try:
        admitted = admit_public_search_cover(
            row, identity_id=identity_id, observed_at=1,
            public_cover_url=row.thumbnail_url,
        )
        if (admitted is not None
                and notice_cover_document_ref(admitted.key) == source):
            if canonical_cover_document_ref(
                admitted.key.edition_id,
            ) == canonical:
                return canonical
            # A true cross-provider target is never derivable from Search
            # title/ISBN/thumbnail/provider fields. Only the Core worker
            # can stamp this private token AFTER verifying two completed,
            # same-owner provider OriginLinks and persisting both sources.
            # No SQL or entitlement lookup is performed on GTK here.
            from .notice_c_origin_link_producer import (
                _CORE_ORIGIN_LINK_C_WITNESS,
            )
            proof = getattr(row, "_core_notice_cross_provider_proof", None)
            epoch = getattr(
                row, "_core_notice_canonical_cover_document_epoch", None,
            )
            if (type(proof) is tuple and len(proof) == 4
                    and proof[0] is _CORE_ORIGIN_LINK_C_WITNESS
                    and proof[1] == source.source_key
                    and proof[2] == canonical.source_key
                    and type(epoch) is int and epoch > 0
                    and proof[3] == epoch):
                return canonical
    except (TypeError, ValueError, UnicodeError):
        pass
    return None


__all__ = ["merge_public_notice_c_known_rows", "matching_live_public_canonical_ref"]
