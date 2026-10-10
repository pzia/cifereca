"""Validate untrusted provider stream events before delivery to Core clients."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..models import SearchResult
from ..plugin_api import ProviderSearchEvent


class SearchStreamFailure(ValueError):
    pass


class SearchStreamCancelled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class StreamResult:
    rows: tuple[SearchResult, ...]
    warnings: tuple[str, ...]
    error_code: str = ""
    error_message: str = ""


MAX_STREAM_EVENTS = 20000
MAX_STREAM_RESULTS = 5000


def consume_provider_stream(
    events: Iterable[ProviderSearchEvent], *,
    provider_id: str,
    catalog_generation: int,
    publish: Callable[[ProviderSearchEvent], None],
    cancelled: Callable[[], bool],
) -> StreamResult:
    """Replay a single catalog generation; no partial result is cached here.

    On a sequence gap or missing 'done', caller should emit 'reset' and obtain
    a fresh provider snapshot through ordinary ProviderSession.search().
    """
    sequence = 0
    rows: dict[str, SearchResult] = {}
    warnings: list[str] = []
    ended = False
    for count, event in enumerate(events, 1):
        if cancelled():
            raise SearchStreamCancelled()
        if count > MAX_STREAM_EVENTS:
            raise SearchStreamFailure("stream event limit exceeded")
        if not isinstance(event, ProviderSearchEvent):
            raise SearchStreamFailure("invalid stream event type")
        if (event.provider_id != provider_id
                or type(event.catalog_generation) is not int
                or event.catalog_generation != catalog_generation):
            raise SearchStreamFailure("stale or mismatched provider catalog generation")
        if type(event.sequence) is not int or event.sequence < 1:
            raise SearchStreamFailure("invalid stream sequence")
        if event.sequence <= sequence:
            continue  # retry/replay of an already applied event
        if event.sequence != sequence + 1:
            raise SearchStreamFailure("stream sequence gap")
        if ended:
            raise SearchStreamFailure("stream event after terminal event")
        sequence = event.sequence
        if event.kind == "upsert":
            row = event.result
            if not isinstance(row, SearchResult) or row.source_provider != provider_id:
                raise SearchStreamFailure("invalid provider-scoped upsert")
            key = f"{row.docbase}:{row.record_id}"
            if not row.docbase or not row.record_id or event.item_key != key:
                raise SearchStreamFailure("upsert item key mismatch")
            if key not in rows and len(rows) >= MAX_STREAM_RESULTS:
                raise SearchStreamFailure("stream result limit exceeded")
            rows[key] = row
        elif event.kind == "remove":
            if not event.item_key:
                raise SearchStreamFailure("remove item key required")
            rows.pop(event.item_key, None)
        elif event.kind == "progress":
            for value in (event.current, event.total):
                if value is not None and (type(value) is not int or value < 0):
                    raise SearchStreamFailure("invalid progress count")
            if event.total is not None and event.current is not None and event.current > event.total:
                raise SearchStreamFailure("progress exceeds total")
        elif event.kind == "error":
            if not event.error_code:
                raise SearchStreamFailure("error_code required")
            warnings.append(f"{provider_id}: {event.error_code}")
        elif event.kind == "done":
            ended = True
        else:
            raise SearchStreamFailure("invalid stream event kind")
        publish(event)
        if ended:
            break
    if cancelled():
        raise SearchStreamCancelled()
    if not ended:
        raise SearchStreamFailure("stream terminal event missing")
    return StreamResult(
        tuple(rows.values()), tuple(warnings),
        "StreamPartialError" if warnings else "",
        "; ".join(warnings),
    )


__all__ = [
    "consume_provider_stream", "SearchStreamCancelled", "SearchStreamFailure",
    "StreamResult",
]
