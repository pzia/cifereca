"""History list/detail cover parity for the same exact displayed history item.

This is a *presentation-only*, window-scoped cache of the result returned by
the existing HistoryCoverResolver. A title-based catalog search is NOT an
attested OriginLink, purchase or loan cover and is never promoted into the
canonical Core N1 documentary cache. Only the same HistoryItem object,
selected profile, account/source/identity epochs and primary account can
reuse it. A different HistoryItem with the same title or record ID cannot.

No provider I/O, GTK imports, entitlement writes or cross-profile joins.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import time
from typing import Any

from ..core.covers import candidate
from ..models import HistoryItem


@dataclass(frozen=True, slots=True)
class HistoryCoverContext:
    identity_id: str
    account_epoch: int
    source_epoch: int
    identity_generation: int


def history_cover_context(window: Any) -> HistoryCoverContext | None:
    service = getattr(window, "service", None)
    if getattr(getattr(service, "current_account", None), "is_primary", None) is not True:
        return None
    identity = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    account_epoch = getattr(service, "_account_context_epoch", None)
    source_epoch = getattr(service, "_source_context_epoch", None)
    generation = getattr(window, "_identity_context_generation", None)
    if (type(identity) is not str or not identity
            or any(type(number) is not int or number < 0
                   for number in (account_epoch, source_epoch, generation))):
        return None
    return HistoryCoverContext(identity, account_epoch, source_epoch, generation)


def _same_current_item(
    window: Any, item: HistoryItem, context: HistoryCoverContext,
) -> bool:
    if (type(item) is not HistoryItem
            or type(item.id) is not str or not item.id
            or item.identity_id not in (None, context.identity_id)
            or history_cover_context(window) != context):
        return False
    objects = getattr(window, "history_objects", None)
    return type(objects) is dict and objects.get(item.id) is item


class _HistoryCoverRecord:
    __slots__ = ("item", "context", "url", "expires")
    def __init__(
        self, item: HistoryItem, context: HistoryCoverContext,
        url: str, expires: float,
    ):
        self.item = item
        self.context = context
        self.url = url
        self.expires = expires


def cached_history_cover(window: Any, item: HistoryItem) -> str | None:
    """Use a previously discovered image only for the same live item."""
    context = history_cover_context(window)
    if context is None or not _same_current_item(window, item, context):
        return None
    memory = getattr(window, "_history_cover_presentation", None)
    if type(memory) is not OrderedDict:
        return None
    record = memory.get(item.id)
    if (type(record) is not _HistoryCoverRecord or record.item is not item
            or record.context != context or time.monotonic() >= record.expires):
        return None
    memory.move_to_end(item.id)
    return record.url


def publish_history_cover(
    window: Any, item: HistoryItem, row: Any, url: str | None, *,
    context: HistoryCoverContext | None,
) -> bool:
    """Paint one still-bound row in place and remember its *own* image.

    The existing HistoryCoverResolver is allowed to run only for the
    current primary account. The result is never reusable across different
    book objects, even if supplier IDs, cover URLs or titles coincide.
    """
    if (type(context) is not HistoryCoverContext
            or not _same_current_item(window, item, context)
            or getattr(row, "key", None) != item.id
            or getattr(row, "_history_source_item", None) is not item
            or not callable(getattr(row, "set_thumbnail_url", None))):
        return False
    cover = candidate(url, tier="catalog", source="history-visual-fallback")
    if cover is None:
        return False
    memory = getattr(window, "_history_cover_presentation", None)
    if type(memory) is not OrderedDict:
        memory = OrderedDict()
        window._history_cover_presentation = memory
    memory[item.id] = _HistoryCoverRecord(
        item, context, cover.url, time.monotonic() + 900.0,
    )
    memory.move_to_end(item.id)
    while len(memory) > 128:
        memory.popitem(last=False)
    row.set_thumbnail_url(cover.url)
    return True


__all__ = [
    "HistoryCoverContext", "history_cover_context",
    "cached_history_cover", "publish_history_cover",
]
