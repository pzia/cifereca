"""#6366/#3724: real, public SearchResult cover -> admitted Notice C source.

Only the already-parsed row supplied by the Core search producer is read.
An image URL alone, a UI key, a title, an ISBN, a profile viewer or an N3
hit cannot establish a documentary association. No network or SQLite here.
"""
from __future__ import annotations

from hashlib import sha256
import json

from ..models import SearchResult
from .notice_source_observation import (
    NoticeSourceKey, NoticeSourceObservation, admit_source_notice,
)
from .search_cover_provenance import project_exact_search_catalogue_cover


def _coordinate_digest(*parts: object) -> str:
    """Opaque, stable coordinates; never a title/ISBN/content deduplication."""
    raw = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return sha256(raw.encode("ascii")).hexdigest()


def admit_public_search_cover(
    row: SearchResult, *, identity_id: str, observed_at: int,
    public_cover_url: str | None,
) -> NoticeSourceObservation | None:
    """Admit one live producer-owned *public* image to the Notice C ABI.

    The caller must supply the URL from the existing Core public catalogue
    guard, not from the GTK widget. The additional search provenance gate
    rejects stale/offline copies, private credentials/accounts, owned and
    downloadable rows. An absent image is UNKNOWN: search-result omission
    does not attest a complete negative inventory.

    Coordinates preserve the producing provider, exact record, document
    base, collection and medium. They do not merge editions or assign rights.
    """
    if (type(row) is not SearchResult
            or type(public_cover_url) is not str or not public_cover_url):
        return None
    qualified = project_exact_search_catalogue_cover(
        row, identity_id=identity_id, share_public=True,
    )
    if (qualified is None
            or qualified.scope.kind != "public_catalogue"
            or qualified.catalogue.url != public_cover_url):
        return None
    key = NoticeSourceKey(
        provider_id=row.source_provider,
        visibility="public",
        identity_id=None, credential_id=None, account_id=None,
        branch_id="search.public.catalogue",
        record_id="search:" + _coordinate_digest(
            row.source_provider, row.source_collection, row.record_id,
        ),
        edition_id="edition:" + _coordinate_digest(
            row.source_provider, row.source_collection, row.record_id,
            row.docbase, row.medium,
        ),
        manifestation_role=(
            "digital" if row.medium == "digital"
            else "print" if row.medium == "physical" else "unknown"
        ),
    )
    # This is the parser's single, present display-image field. No absence
    # or exhaustive origin/EPUB-cover inventory is asserted by a search hit.
    return admit_source_notice(
        key=key, parser_id="core.search.public-cover",
        parser_version="1", observed_at=observed_at,
        raw_fields={"search_result": {"thumbnail_url": public_cover_url}},
        status="complete",
    )


__all__ = ["admit_public_search_cover"]
