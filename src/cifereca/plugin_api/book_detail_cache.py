"""Bounded cache-only contributions to an existing Core book-detail view.

This worker-side adapter never performs a search or initiates cache refreshes.
A slow or broken plugin cannot stall the GTK render path: calls are isolated
in daemon threads, with a process-wide concurrency ceiling and a short budget.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from threading import BoundedSemaphore, Condition, Event, Lock, Thread
from time import monotonic
from typing import Callable, Iterable

from cifereca.catalogue_link import safe_document_link_url

from .models import (
    BookRef, CachedBookDetail, DetailField, DetailSection, Provenance,
    ResolvedPluginService, UiBookDetailAction,
)


# Deliberately no unbounded shared queue. A plugin stuck in cached_detail()
# occupies at most one of these slots; future requests skip the busy slot.
_CACHE_READ_SLOTS = BoundedSemaphore(4)
# Registration and teardown are plugin code too: neither may block GTK.
_CACHE_SUBSCRIPTION_SLOTS = BoundedSemaphore(4)
_CACHE_UNSUBSCRIBE_SLOTS = BoundedSemaphore(4)


@dataclass(frozen=True, slots=True)
class CachedBookDetailContribution:
    plugin_id: str
    detail: CachedBookDetail


def _small_text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _sanitize(detail: object, book: BookRef, identity_id: str, plugin_id: str) -> CachedBookDetail | None:
    if not isinstance(detail, CachedBookDetail):
        return None
    if not book.book_id or detail.book_id != book.book_id or detail.identity_id != identity_id:
        return None
    sections = []
    for section in detail.sections[:3]:
        if not isinstance(section, DetailSection) or not section.id or not section.title:
            continue
        fields = []
        for field in section.fields[:8]:
            if not isinstance(field, DetailField) or not field.id or not field.label:
                continue
            fields.append(replace(
                field, id=_small_text(field.id, 80),
                label=_small_text(field.label, 80),
                value=_small_text(field.value, 280),
            ))
        if fields:
            sections.append(replace(
                section, id=_small_text(section.id, 80),
                title=_small_text(section.title, 100), fields=tuple(fields),
            ))
    actions = []
    for action in detail.actions[:4]:
        if not isinstance(action, UiBookDetailAction) or not action.id or not action.title:
            continue
        uri = safe_document_link_url(action.uri, relation="related") if action.uri else ""
        if action.uri and not uri:
            continue
        if not uri and not callable(action.callback):
            continue
        actions.append(replace(
            action, id=_small_text(action.id, 80),
            title=_small_text(action.title, 100), uri=uri or "",
        ))
    if not sections and not actions:
        return None
    return replace(
        detail, sections=tuple(sections), actions=tuple(actions),
        source=detail.source if isinstance(detail.source, Provenance)
        else Provenance(source=plugin_id),
        updated_at=_small_text(detail.updated_at, 48),
        freshness_token=_small_text(detail.freshness_token, 128),
    )


def collect_cached_book_details(
    services: Iterable[ResolvedPluginService],
    book: BookRef,
    *,
    identity_id: str,
    budget_ms: int = 25,
) -> tuple[CachedBookDetailContribution, ...]:
    """Collect up to four cache-only contributions, in stable plugin-id order.

    This bounded worker operation waits at most budget_ms (capped at 100 ms).
    It is intended to run from the application's task runner, not GTK itself.
    Calls made by plugins must remain cache-only; Core cannot prevent a plugin
    from making an unauthorized network call, but it never waits for one.
    """
    if not identity_id or not isinstance(book, BookRef) or not book.book_id:
        return ()
    budget = min(100, max(0, int(budget_ms))) / 1000.0
    deadline = monotonic() + budget
    ordered = {}
    for resolved in services:
        plugin_id = getattr(resolved, "plugin_id", "")
        service = getattr(resolved, "service", None)
        if plugin_id and callable(getattr(service, "cached_detail", None)):
            ordered.setdefault(plugin_id, service)

    condition = Condition()
    pending = [0]
    accepted: dict[str, CachedBookDetail] = {}

    def read(plugin_id: str, service: object) -> None:
        try:
            try:
                detail = service.cached_detail(book, identity_id=identity_id)
                safe = _sanitize(detail, book, identity_id, plugin_id)
            except Exception:
                safe = None
            with condition:
                if safe is not None and monotonic() <= deadline:
                    accepted[plugin_id] = safe
                pending[0] -= 1
                condition.notify_all()
        finally:
            _CACHE_READ_SLOTS.release()

    for plugin_id, service in sorted(ordered.items())[:4]:
        if monotonic() >= deadline or not _CACHE_READ_SLOTS.acquire(blocking=False):
            break
        with condition:
            pending[0] += 1
        try:
            Thread(
                target=read, args=(plugin_id, service), daemon=True,
                name="cifereca-detail-cache",
            ).start()
        except RuntimeError:
            with condition:
                pending[0] -= 1
            _CACHE_READ_SLOTS.release()

    with condition:
        while pending[0] and monotonic() < deadline:
            condition.wait(timeout=max(0.0, deadline - monotonic()))
        result = tuple(
            CachedBookDetailContribution(plugin_id, accepted[plugin_id])
            for plugin_id in sorted(accepted)
        )
    return result


def subscribe_cached_book_detail_changes(
    services: Iterable[ResolvedPluginService], callback: Callable[[], None],
) -> Callable[[], None]:
    """Subscribe without executing any third-party callback on the GTK thread.

    At most four registration calls run concurrently across views. A hung
    plugin cannot block rendering or create an unlimited number of threads.
    Callers can close immediately, even before registration has completed;
    a late registration is then unsubscribed rather than leaked. Callbacks
    already queued when the view closes become inert.
    """
    closed = Event()
    mutex = Lock()
    unsubscribe: list[Callable[[], None]] = []

    def notify() -> None:
        if not closed.is_set():
            callback()

    def close_in_background(close: Callable[[], None]) -> None:
        # Even unsubscribe_cache can run arbitrary plugin code; never invoke
        # it from the view's close handler. A saturated pool is fail-closed:
        # notify() stays inert if a malicious plugin never finishes cleanup.
        if not _CACHE_UNSUBSCRIBE_SLOTS.acquire(blocking=False):
            return

        def run() -> None:
            try:
                try:
                    close()
                except Exception:
                    pass
            finally:
                _CACHE_UNSUBSCRIBE_SLOTS.release()

        try:
            Thread(target=run, name="cifereca-cache-unsubscribe", daemon=True).start()
        except RuntimeError:
            _CACHE_UNSUBSCRIBE_SLOTS.release()

    def register(subscribe: Callable) -> None:
        try:
            close = subscribe(notify)
            if not callable(close):
                return
            with mutex:
                if closed.is_set():
                    late = True
                else:
                    unsubscribe.append(close)
                    late = False
            if late:
                close_in_background(close)
            else:
                # Catch cache updates between the initial detail render and
                # asynchronous subscription registration.
                try:
                    notify()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            _CACHE_SUBSCRIPTION_SLOTS.release()

    ordered: dict[str, Callable] = {}
    for resolved in services:
        plugin_id = str(getattr(resolved, "plugin_id", "") or "")
        subscribe = getattr(getattr(resolved, "service", None), "subscribe_cache", None)
        if plugin_id and callable(subscribe):
            ordered.setdefault(plugin_id, subscribe)

    for subscribe in (ordered[plugin_id] for plugin_id in sorted(ordered)[:4]):
        if not _CACHE_SUBSCRIPTION_SLOTS.acquire(blocking=False):
            break
        try:
            Thread(
                target=register, args=(subscribe,),
                name="cifereca-cache-subscribe", daemon=True,
            ).start()
        except RuntimeError:
            _CACHE_SUBSCRIPTION_SLOTS.release()

    def close_all() -> None:
        with mutex:
            closed.set()
            closers = tuple(unsubscribe)
            unsubscribe.clear()
        for close in reversed(closers):
            close_in_background(close)

    return close_all


__all__ = [
    "CachedBookDetailContribution", "collect_cached_book_details",
    "subscribe_cached_book_detail_changes",
]
