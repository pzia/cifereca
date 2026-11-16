"""#6366: exact public C cover for an already-admitted known Search recommendation.

This read-only Core worker adapter consumes the exact v2 mirrored Search
source vetted by known_search_notice_navigation; it never trusts a plugin's
title/ISBN/thumbnail or assumes a known public catalogue grants a loan.
"""
from __future__ import annotations

from typing import Any

from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import is_canonical_cover_document
from .notice_c_exact_source_lookup import exact_public_source_c


def known_recommendation_public_c_document(
    storage: Any, row: object, *, identity_id: str,
    provider: str, credential: str, record_id: str, docbase: str,
    source_collection: str,
) -> CoverDocumentScope | None:
    """Return C solely for a verified, exact, anonymous public v2 source.

    A v3/revisioned mirror or an account-bound offer is not this contract:
    their own qualified source routes must publish their versioned refs.
    Call only on the existing holdings worker, never a GTK callback.
    """
    if type(row) is not dict or type(identity_id) is not str or not identity_id:
        return None
    metadata = row.get("metadata")
    if type(metadata) is not dict:
        return None
    if (row.get("source") != "known_catalog"
            or row.get("identity_id") != identity_id
            or row.get("provider_id") != provider
            or row.get("item_id") != record_id
            or row.get("local") is not False
            or credential != "__public__"
            or source_collection != "public_catalog"
            or metadata.get("catalogue_scope_version") != 2
            or metadata.get("source_credential") != credential
            or metadata.get("source_collection") != source_collection
            or metadata.get("docbase") != docbase
            or metadata.get("source_account_id") not in (None, "")
            or metadata.get("source_revision") is not None
            or metadata.get("search_provenance") == "known_catalog_stale"
            or metadata.get("medium") not in ("digital", "physical")
            or row.get("state") != "remote_catalog"):
        return None
    # Only a stored C source from a public Search observation can establish
    # the edition relation; even an identical provider cover URL cannot.
    consolidated = exact_public_source_c(
        storage, provider_id=provider, record_id=record_id,
        docbase=docbase, medium=metadata["medium"], source_revision=None,
    )
    if (consolidated is None
            or not is_canonical_cover_document(consolidated.document)):
        return None
    return consolidated.document


__all__ = ["known_recommendation_public_c_document"]
