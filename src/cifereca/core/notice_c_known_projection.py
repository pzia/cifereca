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
from dataclasses import asdict

from .notice_c_public_search import PublicNoticeCPage, PublicNoticeCHit
from .notice_cover_repository import is_notice_cover_document
from .notice_c_cover_index import _MAX_SOURCES

_MAX = 50


def _public_mirror_key(row: dict[str, Any], *, identity_id: str) -> tuple | None:
    """Only an exact, unpossessed, descriptive anonymous mirror is redundant."""
    if (type(row) is not dict
            or row.get("source") != "known_catalog"
            or row.get("identity_id") != identity_id
            or row.get("local") is not False
            or any(row.get(name) for name in ("placement_id", "artifact_id", "local_path"))
            or row.get("state") not in ("remote_catalog", "available_to_buy")):
        return None
    meta = row.get("metadata")
    if (type(meta) is not dict
            or type(meta.get("catalogue_scope_version")) is not int
            or meta.get("catalogue_scope_version") not in (2, 3)
            or meta.get("source_credential") != "__public__"
            or meta.get("source_collection") != "public_catalog"
            or meta.get("source_account_id") not in (None, "")):
        return None
    revision = meta.get("source_revision")
    if meta["catalogue_scope_version"] == 2:
        if revision not in (None, ""):
            return None
        revision = None
    elif type(revision) is not str or not revision:
        return None
    coords = (row.get("provider_id"), row.get("item_id"),
              meta.get("docbase"), meta.get("medium"))
    if any(type(value) is not str or not value for value in coords):
        return None
    return (*coords, revision)


def _series_metadata(hit: PublicNoticeCHit) -> dict[str, Any]:
    metadata, proofs = {}, {}
    if hit.series_relation is not None:
        proof = asdict(hit.series_relation)
        metadata["series"] = [proof.pop("label")]
        proofs["series"] = proof
    if hit.language_observation is not None:
        proof = hit.language_observation.wire()
        metadata["languages"] = proof.pop("languages")
        proofs["languages"] = proof
    if proofs:
        metadata["bibliography_evidence"] = proofs
    return metadata


def merge_public_notice_c_known_rows(
    known: list[dict[str, Any]], page: PublicNoticeCPage,
    *, identity_id: str,
) -> list[dict[str, Any]]:
    """Present known C before the mirror; source C never conveys rights.

    The C query cursor limits itself to 50 known documents or source facts, so this first
    stage has no claim to full query coverage. An empty page is unknown, not
    evidence that a provider has zero documents.
    """
    if (type(known) is not list or type(page) is not PublicNoticeCPage
            or type(identity_id) is not str or not identity_id
            or len(page.hits) > _MAX):
        raise ValueError("invalid_known_public_c_projection")

    hits = page.hits
    if page.known_documents is not None:
        # A grouped page can carry up to 32 exact sources per visible C.
        # Do not reintroduce an unrelated group or silently truncate its
        # admitted matching alternatives when suppressing public mirrors.
        hits = page.matching_sources
        if (type(hits) is not tuple or len(hits) > _MAX * _MAX_SOURCES
                or any(type(hit) is not PublicNoticeCHit for hit in hits)
                or any(type(hit) is not PublicNoticeCHit for hit in page.hits)
                or {hit.document for hit in hits} != {hit.document for hit in page.hits}):
            raise ValueError("invalid_grouped_public_c_projection")
    rows: list[dict[str, Any]] = []
    admitted: set[tuple] = set()
    by_document: dict[str, dict[str, Any]] = {}
    for hit in hits:
        if (type(hit) is not PublicNoticeCHit
                or hit.document.kind != "public_catalogue"
                or hit.document.identity_id is not None
                or not is_notice_cover_document(hit.document)):
            continue
        admitted.add((hit.provider_id, hit.record_id, hit.docbase,
                      hit.medium, hit.source_revision))
        prior = by_document.get(hit.document.source_key)
        if prior is not None:
            # Distinct providers can contribute different public text to ONE
            # canonical cover document. Keep their independently attested
            # descriptions as alternatives; never silently treat the first
            # source's words as a reconciled canonical bibliography.
            alternatives = prior["metadata"].setdefault(
                "notice_c_source_alternatives", [],
            )
            alternatives.append({
                "provider_id": hit.provider_id,
                "record_id": hit.record_id,
                "source_revision": hit.source_revision,
                "title": hit.title,
                "author": hit.author,
                "publisher": hit.publisher,
                "description": hit.description,
                **({"published_at": hit.published_at} if hit.published_at is not None else {}),
                **_series_metadata(hit),
            })
            continue
        rows.append({
            "key": "c-public:" + hit.document.source_key,
            "source": "notice_c_public",
            "identity_id": identity_id,
            "provider_id": hit.provider_id,
            "item_id": hit.record_id,
            "title": hit.title,
            "author": hit.author,
            "published_at": hit.published_at,
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
                **_series_metadata(hit),
            },
            "_cover_document_ref": hit.document,
        })
        by_document[hit.document.source_key] = rows[-1]
    for row in known:
        # Do not replace local placements, verified rights, private sources,
        # different revisions or medium with public descriptive metadata.
        if _public_mirror_key(row, identity_id=identity_id) not in admitted:
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
