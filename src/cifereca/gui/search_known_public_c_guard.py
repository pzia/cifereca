"""#6366: exact Search C-first row N3 guard, on lists and book details.

The public C document itself is not private. Its association with a selected
Search row still depends on the current query, identity and cached C epoch.
This is a pure GTK-side validation of previously materialised Core evidence:
no SQLite, HTTP, local-file lookup, URL/title/ISBN equality or rights decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.book_cover_projection import CoverDocumentScope
from ..core.notice_cover_repository import is_notice_cover_document


@dataclass(frozen=True, slots=True)
class PinnedKnownPublicC:
    source_item: dict[str, Any] = field(compare=False, repr=False)
    service: object = field(compare=False, repr=False)
    row_key: str
    document: CoverDocumentScope
    epoch: int
    identity_id: str
    # Mode matters: a local→global Search transition invalidates the
    # previous C-first cell even with identical title/URL/DocumentRef.
    context: tuple[object, ...]


def _context(window: Any, service: object) -> tuple[object, ...]:
    return (
        getattr(window, "_search_mode", None),
        getattr(window, "_identity_context_generation", None),
        getattr(window, "_search_generation", None),
        getattr(service, "_account_context_epoch", None),
        getattr(service, "_source_context_epoch", None),
    )


def pin_known_public_c(
    window: Any, row: Any, item: object,
) -> PinnedKnownPublicC | None:
    """Pin a *currently mapped* Search C known row, never a raw URL/ref."""
    if type(item) is not dict or item.get("source") != "notice_c_public":
        return None
    service = getattr(window, "service", None)
    identity_id = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    key = item.get("key")
    document = item.get("_cover_document_ref")
    meta = item.get("metadata")
    epoch = meta.get("notice_c_epoch") if type(meta) is dict else None
    context = _context(window, service)
    source_rows = getattr(window, "_search_local_objects", None)
    if (getattr(window, "_closed", False) is True
            or type(identity_id) is not str or not identity_id
            or type(key) is not str or not key or len(key) > 1500
            or type(row) is object or getattr(row, "key", None) != key
            or type(source_rows) is not dict or source_rows.get(key) is not item
            or item.get("identity_id") != identity_id
            or type(document) is not CoverDocumentScope
            or document.kind != "public_catalogue"
            or document.identity_id is not None
            or not is_notice_cover_document(document)
            or type(epoch) is not int or epoch < 1
            or len(context) != 5 or context[0] not in ("local", "global")
            or any(type(generation) is not int or generation < 0
                   for generation in context[1:])):
        return None
    # A genuine BookListItem can carry a witness for a different same-key
    # source. Absence is permitted only for an unmaterialised detail adapter
    # (the N3 path itself will still be verified at callback time).
    witnessed = getattr(row, "_cover_known_public_c_source", None)
    if witnessed is not None and witnessed is not item:
        return None
    displayed = getattr(row, "_cover_document_ref", None)
    if displayed is not None and displayed != document:
        return None
    displayed_epoch = getattr(row, "_cover_public_c_epoch", None)
    if displayed_epoch is not None and displayed_epoch != epoch:
        return None
    return PinnedKnownPublicC(
        source_item=item, service=service, row_key=key,
        document=document, epoch=epoch, identity_id=identity_id,
        context=context,
    )


def known_public_c_current(window: Any, binding: object) -> bool:
    """N3 and post-GDK N4 check: refuse ABA, same-key and C epoch replacement."""
    if type(binding) is not PinnedKnownPublicC:
        return False
    service = getattr(window, "service", None)
    if (getattr(window, "_closed", False) is True
            or service is not binding.service
            or _context(window, service) != binding.context
            or getattr(getattr(service, "current_identity", None),
                       "identity_id", None) != binding.identity_id):
        return False
    item = binding.source_item
    rows = getattr(window, "_search_local_objects", None)
    meta = item.get("metadata") if type(item) is dict else None
    return (
        type(rows) is dict
        and rows.get(binding.row_key) is item
        and item.get("key") == binding.row_key
        and item.get("source") == "notice_c_public"
        and item.get("identity_id") == binding.identity_id
        and item.get("_cover_document_ref") == binding.document
        and type(meta) is dict
        and type(meta.get("notice_c_epoch")) is int
        and meta["notice_c_epoch"] == binding.epoch
        and is_notice_cover_document(binding.document)
    )


def known_public_c_cell_current(window: Any, row: Any, binding: object) -> bool:
    """A recycled list cell may paint only its original C-first source."""
    return (
        known_public_c_current(window, binding)
        and getattr(row, "key", None) == binding.row_key
        and getattr(row, "_cover_known_public_c_source", None)
            is binding.source_item
        and getattr(row, "_cover_document_ref", None) == binding.document
        and getattr(row, "_cover_public_c_epoch", None) == binding.epoch
    )


__all__ = [
    "PinnedKnownPublicC", "pin_known_public_c",
    "known_public_c_current", "known_public_c_cell_current",
]
