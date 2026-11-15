"""#2385/#6168: Core SQLite Notice C source -> derived N2 on cache miss.

This adapter is only a bridge between the Core durable source port and the
N2 selection cache. It does not parse supplier responses, read EPUBs, build
book identity from an image URL, or use the viewing profile as cache scope.
The DocumentCoverService invokes it on a bounded worker, never on GTK bind.
"""
from __future__ import annotations

from typing import Any

from ..core.book_cover_projection import CoverDocumentScope
from ..core.notice_cover_repository import read_notice_cover
from .document_cover_service import CoverSourceSnapshot


def cached_notice_cover_snapshot(
    service: Any, document: CoverDocumentScope,
) -> CoverSourceSnapshot | None:
    """Read exactly one admitted source, not the selected profile catalogue."""
    if (type(document) is not CoverDocumentScope
            or document.kind not in ("catalogue", "public_catalogue")
            or not document.source_key.startswith("notice-c:v1:")):
        return None
    storage = getattr(service, "storage", None)
    if storage is None:
        return None
    stored = read_notice_cover(storage, document)
    if stored is None:
        # No stored source != a confirmed empty cover inventory.
        return None
    return CoverSourceSnapshot(
        scope=stored.document, revision=stored.epoch,
        candidates=stored.attribute.candidates,
        snapshot_complete=stored.attribute.snapshot_complete,
    )


__all__ = ["cached_notice_cover_snapshot"]
