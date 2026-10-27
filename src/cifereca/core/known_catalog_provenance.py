"""Read-only provenance gate for opening a mirrored catalogue notice (#1354).

A historical cache entry is useful for SEARCH, but not evidence of its exact
provider session or edition. Only Core's versioned, scope-qualified mirror can
reconstruct a SearchResult for an EXPLICIT holdings request. Never look up or
contact a provider, grant a right, or silently supply a default credential.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..models import SearchResult


def _coordinate(value: Any, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return ""
    if (type(value) is not str or len(value) > 512
            or (not optional and not value)
            or "\x00" in value):
        return None
    return value


def known_catalog_search_result(
    row: Mapping[str, Any], *, identity_id: str | None,
) -> SearchResult | None:
    """Rebuild an exact source-scoped notice or refuse all remote actions.

    A remote notice may remain visible and searchable when this returns None.
    It must then be refreshed online before holdings or a download is offered.
    """
    if type(row) is not dict or type(identity_id) is not str or not identity_id:
        return None
    if row.get("source") != "known_catalog" or row.get("identity_id") != identity_id:
        return None
    meta = row.get("metadata")
    if type(meta) is not dict or meta.get("catalogue_scope_version") != 2:
        return None
    if meta.get("search_provenance") == "known_catalog_stale":
        return None
    provider = _coordinate(row.get("provider_id"))
    item_id = _coordinate(row.get("item_id"))
    credential = _coordinate(meta.get("source_credential"))
    docbase = _coordinate(meta.get("docbase"))
    collection = _coordinate(meta.get("source_collection"), optional=True)
    key = row.get("key")
    if (provider in (None, "local") or not item_id or not credential
            or not docbase or collection is None
            or type(key) is not str or not key.startswith("known:")
            or len(key) > 512 or type(row.get("title")) is not str):
        return None

    # Older caches may contain an arbitrary serialized search_result from a
    # different user/session/edition. Do not replay it; the mirrored fields
    # below were projected by Core from the exact scoped source row.
    medium = meta.get("medium")
    if medium not in ("digital", "physical"):
        return None
    return SearchResult(
        id=key, record_id=item_id, docbase=docbase, medium=medium,
        title=row["title"], author=row.get("author"),
        type=meta.get("type"), format=row.get("media_format"),
        publisher=meta.get("publisher"), published_at=meta.get("published_at"),
        description=meta.get("description"), thumbnail_url=row.get("thumbnail_url"),
        friendly_url=None, scenario=str(meta.get("scenario") or ""),
        source_provider=provider, source_credential=credential,
        source_collection=collection or None, owned=None,
        downloadable=None, download_url=None, metadata={
            "catalogue_scope_version": 2,
            "known_catalog_identity_id": identity_id,
        },
    )
