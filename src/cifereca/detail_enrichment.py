"""Bounded, error-isolated cache-only enrichment for the standard book detail.

Run collect() in a Core task worker, never on GTK's main thread. The executor
has a fixed worker count, a short response deadline, and at most one outstanding
call per plugin instance. A misbehaving plugin cannot create unbounded threads.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import Lock
from time import monotonic
from typing import Any, Callable, Iterable

from .catalogue_link import safe_document_link_url
from .plugin_api.models import (
    BookRef, CachedBookDetail, DetailField, DetailSection, Provenance,
    UiBookDetailAction,
)

DETAIL_ENRICHMENT_CATEGORY = "book.detail.enrichment"


def _text(value: object, limit: int) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return " ".join(str(value).split())[:limit]


def _valid_result(
    result: object, book: BookRef, identity_id: str, plugin_id: str,
) -> CachedBookDetail | None:
    if not isinstance(result, CachedBookDetail):
        return None
    if result.book_id != book.book_id or result.identity_id != identity_id:
        return None
    provenance = result.source if isinstance(result.source, Provenance) else None
    source = _text(provenance.source, 80) if provenance else plugin_id
    if not source:
        source = plugin_id
    sections: list[DetailSection] = []
    for section in result.sections[:2]:
        if not isinstance(section, DetailSection):
            continue
        title = _text(section.title, 80)
        if not title:
            continue
        fields: list[DetailField] = []
        for field in section.fields[:5]:
            if not isinstance(field, DetailField):
                continue
            label = _text(field.label, 64)
            value = _text(field.value, 280)
            if not label or not value:
                continue
            fields.append(DetailField(
                id=_text(field.id, 64), label=label, value=value, kind="text",
                provenance=field.provenance if isinstance(field.provenance, Provenance) else provenance,
            ))
        if fields:
            suffix = " · périmé" if result.stale else ""
            sections.append(DetailSection(
                id=f"{plugin_id}:{_text(section.id, 64)}",
                title=f"{title} · {source}{suffix}"[:160],
                fields=tuple(fields),
                order=section.order if isinstance(section.order, int) else 100,
            ))
    actions: list[UiBookDetailAction] = []
    for action in result.actions[:2]:
        if not isinstance(action, UiBookDetailAction):
            continue
        title = _text(action.title, 80)
        if not title:
            continue
        raw_url = action.uri
        if raw_url:
            uri = safe_document_link_url(raw_url, relation="related")
            if not uri:
                continue
        else:
            uri = ""
        callback = action.callback if callable(action.callback) else None
        if not uri and callback is None:
            continue
        actions.append(UiBookDetailAction(
            id=f"{plugin_id}:{_text(action.id, 64)}",
            title=title, icon_name=_text(action.icon_name, 64) or "view-more-symbolic",
            uri=uri, callback=callback,
        ))
    if not sections and not actions:
        return None
    return CachedBookDetail(
        book_id=book.book_id, identity_id=identity_id,
        sections=tuple(sections), actions=tuple(actions),
        source=provenance,
        updated_at=_text(result.updated_at, 40),
        freshness_token=_text(result.freshness_token, 100),
        stale=bool(result.stale),
    )


class CachedDetailCollector:
    """Collect a fixed number of cached plugin details under a short deadline.

    A timeout does not kill code already executing inside a plugin; instead
    its result is ignored and the next requests do not schedule more work
    for that plugin until the outstanding call ends. Use only for local-cache
    methods and call collect() from a background Core task.
    """

    def __init__(self, *, max_workers: int = 3) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), 4)),
                                        thread_name_prefix="book-detail-cache")
        self._pending: dict[tuple[str, int], Future[Any]] = {}
        self._lock = Lock()
        self._closed = False

    def collect(
        self, services: Iterable[Any], book: BookRef, *,
        identity_id: str, budget_ms: int = 75,
    ) -> tuple[CachedBookDetail, ...]:
        if not isinstance(book, BookRef) or not identity_id:
            return ()
        budget = max(0, min(int(budget_ms), 250)) / 1000
        deadline = monotonic() + budget
        requests: list[tuple[str, tuple[str, int], Future[Any]]] = []
        # Sorting by id avoids priority/resolution-order dependent output.
        known = sorted(
            ((str(getattr(row, "plugin_id", "")), getattr(row, "service", None))
             for row in services),
            key=lambda item: item[0],
        )
        for plugin_id, service in known[:6]:
            callback = getattr(service, "cached_detail", None)
            if not plugin_id or not callable(callback):
                continue
            key = (plugin_id, id(service))
            with self._lock:
                if self._closed:
                    break
                prior = self._pending.get(key)
                if prior is not None and not prior.done():
                    continue
                future = self._pool.submit(callback, book, identity_id=identity_id)
                self._pending[key] = future
            requests.append((plugin_id, key, future))
        if requests and monotonic() < deadline:
            wait([future for _id, _key, future in requests], timeout=max(0, deadline-monotonic()))
        out: list[CachedBookDetail] = []
        for plugin_id, key, future in requests:
            if not future.done():
                continue
            with self._lock:
                if self._pending.get(key) is future:
                    self._pending.pop(key, None)
            try:
                result = _valid_result(future.result(), book, identity_id, plugin_id)
            except Exception:
                # One broken cache plugin must not break other plugin details.
                continue
            if result is not None:
                out.append(result)
        return tuple(out)

    def subscribe(
        self, services: Iterable[Any], callback: Callable[[], None],
    ) -> Callable[[], None]:
        """Subscribe to optional cache-change events; callers dispatch to GTK."""
        unsubscribers: list[Callable[[], None]] = []
        seen: set[int] = set()
        for row in services:
            service = getattr(row, "service", None)
            if id(service) in seen:
                continue
            seen.add(id(service))
            subscribe = getattr(service, "subscribe_cache", None)
            if not callable(subscribe):
                continue
            try:
                unsubscribe = subscribe(callback)
                if callable(unsubscribe):
                    unsubscribers.append(unsubscribe)
            except Exception:
                continue
        def cancel() -> None:
            while unsubscribers:
                try:
                    unsubscribers.pop()()
                except Exception:
                    pass
        return cancel

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)


__all__ = ["DETAIL_ENRICHMENT_CATEGORY", "CachedDetailCollector"]
