"""CoreUI-issued opaque epoch receipt for public plugin book presentations.

The old public port yields a DocumentRef without a revision. The optional
binding port carries the never-serialized, originally witnessed Core result
with its persisted C revision, viewer and owner context. No SQLite, network,
rights decisions or source registration occur here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.book_cover_projection import CoverDocumentScope
from ..core.notice_c_known_projection import matching_live_public_canonical_ref


_ISSUER = object()


@dataclass(frozen=True, slots=True)
class _PublicCBinding:
    issuer: object = field(repr=False)
    window: object = field(repr=False, compare=False)
    service: object = field(repr=False, compare=False)
    source: object = field(repr=False, compare=False)
    # Equality is useful for progressive plugin publication without reading
    # internals: unchanged source/owner/epoch compares equal; a new one does
    # not. Keep references to prevent id reuse.
    window_id: int
    service_id: int
    source_id: int
    identity_id: str
    account_epoch: int
    source_epoch: int
    document: CoverDocumentScope
    epoch: int


def issue_public_c_binding(
    window: Any, item: object, *, identity_id: str,
) -> tuple[CoverDocumentScope, object] | None:
    service = getattr(window, "service", None)
    active = getattr(getattr(service, "current_identity", None), "identity_id", None)
    account_epoch = getattr(service, "_account_context_epoch", None)
    source_epoch = getattr(service, "_source_context_epoch", None)
    if (getattr(window, "_closed", False)
            or type(identity_id) is not str or not identity_id
            or active != identity_id
            or type(account_epoch) is not int or account_epoch < 0
            or type(source_epoch) is not int or source_epoch < 0):
        return None
    document = matching_live_public_canonical_ref(item, identity_id=identity_id)
    epoch = getattr(item, "_core_notice_canonical_cover_document_epoch", None)
    if (document is None or type(epoch) is not int or not 1 <= epoch < 2**63):
        return None
    return document, _PublicCBinding(
        issuer=_ISSUER, window=window, service=service, source=item,
        window_id=id(window), service_id=id(service), source_id=id(item),
        identity_id=identity_id, account_epoch=account_epoch,
        source_epoch=source_epoch, document=document, epoch=epoch,
    )


def public_c_binding_revision(
    window: Any, document: object, receipt: object,
) -> int | None:
    """Revalidate the opaque, original Core receipt, never a plugin integer."""
    if (type(receipt) is not _PublicCBinding
            or receipt.issuer is not _ISSUER
            or receipt.window is not window
            or getattr(window, "_closed", False)
            or receipt.service is not getattr(window, "service", None)
            or receipt.document != document):
        return None
    # The original live SearchResult is mutable. A new Core stamp, provider
    # coordinates, or owner/source generation cannot revive an old receipt.
    service = receipt.service
    if (getattr(getattr(service, "current_identity", None), "identity_id", None)
            != receipt.identity_id
            or getattr(service, "_account_context_epoch", None) != receipt.account_epoch
            or getattr(service, "_source_context_epoch", None) != receipt.source_epoch
            or getattr(receipt.source, "_core_notice_canonical_cover_document_epoch", None)
                != receipt.epoch
            or matching_live_public_canonical_ref(
                receipt.source, identity_id=receipt.identity_id,
            ) != document):
        return None
    return receipt.epoch
