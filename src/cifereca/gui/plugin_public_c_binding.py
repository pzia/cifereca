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
from ..core.notice_c_live_revision import persisted_live_c_revision
from ..core.notice_c_recommendation_known import known_public_c_binding_facts


from ..core.notice_c_cached_revision import matching_cached_public_canonical_ref


def _matching_ref(item, *, identity_id):
    return (matching_live_public_canonical_ref(item, identity_id=identity_id)
            or matching_cached_public_canonical_ref(item, identity_id=identity_id))


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


def _binding_facts(item: object, identity_id: str):
    known = known_public_c_binding_facts(item)
    if known is not None:
        return known
    document = _matching_ref(item, identity_id=identity_id)
    epoch = persisted_live_c_revision(item, document)
    return (document, epoch) if document is not None and epoch is not None else None


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
    facts = _binding_facts(item, identity_id)
    if facts is None:
        return None
    document, epoch = facts
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
            or _binding_facts(receipt.source, receipt.identity_id)
                != (document, receipt.epoch)):
        return None
    return receipt.epoch
