"""Exact SearchResult-to-catalogue-cover source for shared N1/N2 (#6168).

A supplier SearchResult may attest the thumbnail it returned for its OWN
edition. It does NOT attest purchase, loan, access to local EPUB, or another
notice's image. Never join by title, ISBN, image URL or just record ID.

This projection is pure, read-only and independent of N3 image availability.
The GUI owns four active identity/query/account/source epochs separately.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json

from ..models import SearchResult
from .book_cover_projection import CoverDocumentScope
from .covers import CoverCandidate, candidate


def _exact(value: object, *, allow_empty: bool = False) -> bool:
    return (
        type(value) is str and len(value) <= 512
        and (allow_empty or bool(value)) and value.isprintable()
    )


def exact_search_cover_scope(
    item: SearchResult, identity_id: str,
) -> CoverDocumentScope | None:
    """Stable edition, medium, source and account coordinates for Search N2.

    Matches the original SearchCoverBridge identity contract. The digest is
    only an opaque reference; it does NOT prove the existence of the book.
    """
    if (type(item) is not SearchResult or not _exact(identity_id)
            or not _exact(item.source_provider)
            or not _exact(item.record_id)
            or not _exact(item.docbase)
            or type(item.medium) is not str
            or item.medium not in ("digital", "physical", "unknown")
            or not _exact(item.source_credential, allow_empty=True)):
        return None
    extras = (
        getattr(item, "source_collection", None),
        getattr(item, "source_account_id", None),
        getattr(item, "revision_id", None),
    )
    if any(value is not None and not _exact(value, allow_empty=True)
           for value in extras):
        return None
    coordinates = (
        item.source_provider, item.source_credential, item.record_id,
        item.docbase, item.medium, *extras,
    )
    digest = sha256(
        json.dumps(coordinates, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return CoverDocumentScope(
        kind="catalogue", identity_id=identity_id,
        source_key="search-exact:" + digest,
        provider_id=item.source_provider,
        credential_id=item.source_credential,
        account_id=item.source_account_id,
    )


@dataclass(frozen=True, slots=True)
class ExactSearchCatalogueCover:
    """Only the provider's own display image: no inferred acquisition."""

    scope: CoverDocumentScope
    catalogue: CoverCandidate


def project_exact_search_catalogue_cover(
    item: SearchResult, *, identity_id: str, share_public: bool = False,
) -> ExactSearchCatalogueCover | None:
    """Qualify only an active source-originated edition's catalogue image.

    Never promote a stale/offline Search notice into a fresh source proof,
    and never use cache N3 existence as evidence of documentary association.
    The source's actual thumbnail URL is a *catalogue* tier, not origin or
    embedded. Holdings can later publish an independently attested origin.
    """
    scope = exact_search_cover_scope(item, identity_id)
    if scope is None:
        return None
    metadata = item.metadata
    if (type(metadata) is not dict
            or metadata.get("search_provenance") == "known_catalog_stale"):
        return None
    proof = candidate(
        item.thumbnail_url, tier="catalog", source="search-result",
        evidence_id=scope.source_key,
    )
    if proof is None:
        return None
    # The anonymous catalogue contract identifies a source independently
    # of the reading profile. A public-looking URL, private catalogue
    # session or account-scoped result cannot opt itself into this key.
    if (share_public and scope.credential_id == "__public__"
            and type(item.source_collection) is str
            and item.source_collection == "public_catalog"
            and item.source_account_id is None
            and (item.owned is None or item.owned is False)
            and (item.downloadable is None or item.downloadable is False)
            and item.download_url is None):
        scope = replace(scope, kind="public_catalogue", identity_id=None)
    return ExactSearchCatalogueCover(scope, proof)


__all__ = [
    "ExactSearchCatalogueCover", "exact_search_cover_scope",
    "project_exact_search_catalogue_cover",
]
