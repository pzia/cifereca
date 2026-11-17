"""#3104/#6168: plugin-neutral document cover binding to the shared N2 cache.

The plugin may pass back a Core-provided opaque DocumentRef. It cannot
create documentary proof by supplying that reference, a raw image URL, ISBN
or title: DocumentCoverService resolves only admitted exact N2 evidence.
No provider/EPUB read, user permission or profile cache namespace here.
"""
from __future__ import annotations

from typing import Any, Callable, Literal

from ..core.book_cover_projection import CoverDocumentScope
from .document_cover_service import DocumentCoverService

CoverVariant = Literal["list", "detail"]


def bind_declared_document_cover(
    cover: Any, loader: Any, service: Any, document: object, *,
    variant: CoverVariant, priority: int,
    is_current: Callable[[], bool] | None = None,
    minimum_revision: int | None = None,
) -> bool:
    """Bind an exact reference, or fail closed without legacy URL fallback."""
    if (type(document) is not CoverDocumentScope
            or type(service) is not DocumentCoverService):
        cover.bind_url(loader, None, priority=priority)
        return False
    if is_current is not None:
        # Library/Recent public C is bound to a private Artifact/Placement.
        # A delayed or replaced selection cannot borrow this public image.
        try:
            current = callable(is_current) and is_current() is True
        except Exception:
            current = False
        if not current:
            cover.bind_url(loader, None, priority=priority)
            return False
    try:
        options = {"variant": variant, "priority": priority}
        if is_current is not None:
            options["is_current"] = is_current
        if minimum_revision is not None:
            options["minimum_revision"] = minimum_revision
        cover.bind_document(service, document, **options)
    except (TypeError, ValueError, RuntimeError):
        cover.bind_url(loader, None, priority=priority)
        return False
    return True


__all__ = ["bind_declared_document_cover"]
