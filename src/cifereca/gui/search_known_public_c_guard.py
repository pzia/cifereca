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


def _native_public_document(doc: object) -> bool:
    # A Core DocumentRef is a frozen dataclass, not an immutable view lease.
    # Check all members before source-key regex or dataclass equality.
    if type(doc) is not CoverDocumentScope:
        return False
    if any(type(getattr(doc, name)) is not str
           or len(getattr(doc, name)) > 512
           or not getattr(doc, name).isprintable()
           for name in ("kind", "source_key", "provider_id", "credential_id",
                        "loan_id", "artifact_id", "placement_id")):
        return False
    return (doc.kind == "public_catalogue" and doc.identity_id is None
            and doc.account_id is None and doc.credential_id == "__public__"
            and bool(doc.provider_id) and not doc.loan_id and not doc.artifact_id
            and not doc.placement_id and is_notice_cover_document(doc))


def _native_context(ctx: object) -> bool:
    return (type(ctx) is tuple and len(ctx) == 5
            and type(ctx[0]) is str and ctx[0] in ("local", "global")
            and all(type(value) is int and value >= 0 for value in ctx[1:]))


def _epoch(meta: object) -> int | None:
    if (type(meta) is not dict or len(meta) > 128
            or any(type(name) is not str for name in meta)):
        return None
    value = meta.get("notice_c_epoch")
    return value if type(value) is int and 1 <= value < 2**63 else None


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
    if (type(item) is not dict or type(item.get("source")) is not str
            or item["source"] != "notice_c_public"):
        return None
    service = getattr(window, "service", None)
    identity_id = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    key = item.get("key")
    document = item.get("_cover_document_ref")
    meta = item.get("metadata")
    epoch = _epoch(meta)
    context = _context(window, service)
    source_rows = getattr(window, "_search_local_objects", None)
    row_key = getattr(row, "key", None)
    if (getattr(window, "_closed", False) is True
            or type(identity_id) is not str or not identity_id
            or type(key) is not str or not key or len(key) > 1500
            or type(row_key) is not str or row_key != key
            or type(source_rows) is not dict or source_rows.get(key) is not item
            or type(item.get("identity_id")) is not str
            or item["identity_id"] != identity_id
            or not _native_public_document(document)
            or epoch is None or not _native_context(context)):
        return None
    # A genuine BookListItem can carry a witness for a different same-key
    # source. Absence is permitted only for an unmaterialised detail adapter
    # (the N3 path itself will still be verified at callback time).
    witnessed = getattr(row, "_cover_known_public_c_source", None)
    if witnessed is not None and witnessed is not item:
        return None
    displayed = getattr(row, "_cover_document_ref", None)
    if displayed is not None and (
            not _native_public_document(displayed) or displayed != document):
        return None
    displayed_epoch = getattr(row, "_cover_public_c_epoch", None)
    if displayed_epoch is not None and (
            type(displayed_epoch) is not int or displayed_epoch != epoch):
        return None
    return PinnedKnownPublicC(
        source_item=item, service=service, row_key=key,
        document=document, epoch=epoch, identity_id=identity_id,
        context=context,
    )


def attach_filtered_known_public_c(
    window: Any, row: Any, projected: object,
) -> None:
    """Restore the original C source after QueryState.filter copies rows.

    QueryState.filter is a presentation projection and may allocate a new
    dict. Equality of those copies is NOT a Core C witness. Require exact
    key, DocumentRef, owner and persisted C epoch to agree with the actual
    _search_local_objects entry before pinning its original object.
    """
    if type(projected) is not dict:
        return
    key = projected.get("key")
    if type(key) is not str or not key or len(key) > 1500:
        row._cover_known_public_c_source = projected
        return
    current = getattr(window, "_search_local_objects", None)
    source = current.get(key) if type(current) is dict else None
    source_meta = source.get("metadata") if type(source) is dict else None
    projected_meta = projected.get("metadata")
    original_doc = source.get("_cover_document_ref") if type(source) is dict else None
    displayed_doc = projected.get("_cover_document_ref")
    original_epoch = _epoch(source_meta)
    displayed_epoch = _epoch(projected_meta)
    matching = (
        type(projected.get("source")) is str
        and projected["source"] == "notice_c_public"
        and type(source) is dict
        and type(source.get("source")) is str
        and source["source"] == "notice_c_public"
        and type(source.get("key")) is str and source["key"] == key
        and type(source.get("identity_id")) is str
        and type(projected.get("identity_id")) is str
        and source["identity_id"] == projected["identity_id"]
        and _native_public_document(original_doc)
        and _native_public_document(displayed_doc)
        and original_doc == displayed_doc
        and original_epoch is not None
        and original_epoch == displayed_epoch
    )
    original = source if matching else projected
    # Retain a veto marker even on rejected projections. No generic
    # DocumentRef branch may render an unpinned C-first source by accident.
    row._cover_known_public_c_source = original
    packet = pin_known_public_c(window, row, original) if matching else None
    if packet is not None:
        row._cover_known_public_c_receipt = packet


def known_public_c_current(window: Any, binding: object) -> bool:
    """N3 and post-GDK N4 check: refuse ABA, same-key and C epoch replacement."""
    if type(binding) is not PinnedKnownPublicC:
        return False
    service = getattr(window, "service", None)
    current_context = _context(window, service)
    current_identity = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    if (getattr(window, "_closed", False) is True
            or service is not binding.service
            or not _native_context(binding.context)
            or not _native_context(current_context)
            or current_context != binding.context
            or type(binding.identity_id) is not str
            or type(current_identity) is not str
            or current_identity != binding.identity_id
            or type(binding.row_key) is not str or not binding.row_key
            or not _native_public_document(binding.document)
            or type(binding.epoch) is not int
            or not 1 <= binding.epoch < 2**63):
        return False
    item = binding.source_item
    rows = getattr(window, "_search_local_objects", None)
    meta = item.get("metadata") if type(item) is dict else None
    document = item.get("_cover_document_ref") if type(item) is dict else None
    return (
        type(rows) is dict
        and rows.get(binding.row_key) is item
        and type(item) is dict
        and type(item.get("key")) is str and item["key"] == binding.row_key
        and type(item.get("source")) is str and item["source"] == "notice_c_public"
        and type(item.get("identity_id")) is str
        and item["identity_id"] == binding.identity_id
        and _native_public_document(document)
        and document == binding.document
        and _epoch(meta) == binding.epoch
    )


def known_public_c_cell_current(window: Any, row: Any, binding: object) -> bool:
    """A recycled list cell may paint only its original C-first source."""
    displayed = getattr(row, "_cover_document_ref", None)
    key = getattr(row, "key", None)
    epoch = getattr(row, "_cover_public_c_epoch", None)
    return (
        known_public_c_current(window, binding)
        and type(key) is str and key == binding.row_key
        and getattr(row, "_cover_known_public_c_source", None)
            is binding.source_item
        and _native_public_document(displayed)
        and displayed == binding.document
        and type(epoch) is int and epoch == binding.epoch
    )


__all__ = [
    "PinnedKnownPublicC", "pin_known_public_c", "attach_filtered_known_public_c",
    "known_public_c_current", "known_public_c_cell_current",
]
