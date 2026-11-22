"""#2385/#6168: Core SQLite Notice C source -> derived N2 on cache miss.

This adapter is only a bridge between the Core durable source port and the
N2 selection cache. It does not parse supplier responses, read EPUBs, build
book identity from an image URL, or use the viewing profile as cache scope.
The DocumentCoverService invokes it on a bounded worker, never on GTK bind.
"""
from __future__ import annotations

from typing import Any

from ..core.book_cover_projection import CoverDocumentScope
from ..core.notice_cover_repository import read_current_notice_cover
from ..core.notice_c_cover_index import (
    is_canonical_cover_document, read_current_consolidated_public_cover,
)
from .document_cover_service import CoverSourceSnapshot


def cached_notice_cover_snapshot(
    service: Any, document: CoverDocumentScope,
) -> CoverSourceSnapshot | None:
    """Read public cover candidates for the shared, account-free loader.

    An exact private source reference carries provenance but no live session
    authority. Keep it out of this read-through, even when its row is current.
    """
    if (type(document) is not CoverDocumentScope
            or any(type(getattr(document, name)) is not str
                   or len(getattr(document, name)) > 512
                   or not getattr(document, name).isprintable() for name in (
                "kind", "source_key", "provider_id", "credential_id",
                "loan_id", "artifact_id", "placement_id",
            ))
            or (document.identity_id is not None
                and (type(document.identity_id) is not str
                     or not 0 < len(document.identity_id) <= 512
                     or not document.identity_id.isprintable()))
            or (document.account_id is not None
                and (type(document.account_id) is not str
                     or len(document.account_id) > 512
                     or not document.account_id.isprintable()))
            or document.kind != "public_catalogue"):
        return None
    # A frozen CoverDocumentScope is not a trust boundary: object.__setattr__
    # can mutate it after construction. Recheck the public anonymous invariant
    # before any SQLite lookup, including the v1 fallback path.
    try:
        document.__post_init__()
    except (TypeError, ValueError):
        return None
    storage = getattr(service, "storage", None)
    if storage is None:
        return None
    if is_canonical_cover_document(document):
        consolidated = read_current_consolidated_public_cover(storage, document)
        if consolidated is None:
            return None
        return CoverSourceSnapshot(
            scope=consolidated.document, revision=consolidated.epoch,
            candidates=consolidated.candidates,
            snapshot_complete=consolidated.snapshot_complete,
            attested_book_identity=consolidated.attested_book_identity,
        )
    if not document.source_key.startswith("notice-c:v1:"):
        return None
    stored = read_current_notice_cover(storage, document)
    if stored is None:
        # No stored source != a confirmed empty cover inventory.
        return None
    return CoverSourceSnapshot(
        scope=stored.document, revision=stored.epoch,
        candidates=stored.attribute.candidates,
        snapshot_complete=stored.attribute.snapshot_complete,
    )


__all__ = ["cached_notice_cover_snapshot"]
