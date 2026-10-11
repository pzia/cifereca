"""Bounded explicit federated Youth collection, source failures isolated.

Worker-only: no GTK, cache mutation, credentials, or implicit HTTP. The caller
provides the public source fetch and parse contracts and applies results to the
current identity on the GTK main thread, only while its generation is current.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import islice
from typing import Callable


MAX_SOURCES = 8
MAX_CONCURRENCY = 2
MAX_PAGES = 2
PAGE_LIMIT = 50
MAX_ROWS = PAGE_LIMIT * MAX_PAGES


@dataclass(frozen=True, slots=True)
class FederatedSource:
    source_id: str
    label: str
    service: object


@dataclass(frozen=True, slots=True)
class FederatedResult:
    source_id: str
    label: str
    rows: tuple[object, ...] = ()
    fresh_for_seconds: int = 0
    next_cursor: str = ""
    pages: int = 0
    error: str = ""  # only non-sensitive fixed status codes


@dataclass(frozen=True, slots=True)
class FederatedReport:
    results: tuple[FederatedResult, ...]
    eligible: int
    skipped: int
    cancelled: bool

    @property
    def succeeded(self) -> int:
        return sum(not row.error for row in self.results)

    @property
    def unavailable(self) -> int:
        return self.skipped + sum(bool(row.error) for row in self.results)


def prepare_sources(registered, *, info: Callable) -> tuple[tuple[FederatedSource, ...], int]:
    """Only registered, active and capable sources; skip duplicate identifiers."""
    sources: list[FederatedSource] = []
    ids: set[str] = set()
    skipped = 0
    for entry in tuple(registered):
        service = getattr(entry, "service", entry)
        # Some registries expose an explicit disabled flag even if a service
        # is listed. An absent flag is not evidence of deactivation.
        if getattr(entry, "enabled", True) is False or getattr(service, "enabled", True) is False:
            skipped += 1
            continue
        if not any(callable(getattr(service, name, None))
                   for name in ("browse", "search", "browse_with_cancellation")):
            skipped += 1
            continue
        try:
            source_id, label = info(entry)
        except Exception:
            skipped += 1
            continue
        source_id = str(source_id or "").strip()
        if not source_id or source_id in ids:
            skipped += 1
            continue
        ids.add(source_id)
        if len(sources) >= MAX_SOURCES:
            skipped += 1
            continue
        # No external exception/ID/label embedded in error diagnostics.
        title = "".join(c for c in str(label or source_id) if c.isprintable())[:80]
        sources.append(FederatedSource(source_id, title or "Source", service))
    return tuple(sources), skipped


def collect_federated(
    sources: tuple[FederatedSource, ...],
    *,
    query: str,
    age: int | None,
    themes: tuple[str, ...],
    fetch: Callable,
    extract: Callable,
    cancelled: Callable[[], bool],
    on_source: Callable[[FederatedResult], None] | None = None,
    skipped: int = 0,
) -> FederatedReport:
    """Collect at most 8 sources x 2 pages x 50, with 2 concurrent workers.

    A malformed/oversized page rejects this source *atomically*; the UI never
    receives partial unvalidated rows from that source. No executor job is
    started when already cancelled, or for invalid/empty queries.
    """
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
        return FederatedReport((), 0, skipped + len(sources), False)
    chosen = sources[:MAX_SOURCES]
    skipped += max(0, len(sources) - len(chosen))
    if cancelled():
        return FederatedReport((), len(chosen), skipped, True)

    def collect(source: FederatedSource) -> FederatedResult:
        if cancelled():
            return FederatedResult(source.source_id, source.label, error="cancelled")
        cursor: str | None = None
        seen: set[str] = set()
        rows: list[object] = []
        freshness = 0
        pages = 0
        try:
            for _ in range(MAX_PAGES):
                if cancelled():
                    return FederatedResult(source.source_id, source.label, error="cancelled")
                raw = fetch(source.service, query, age=age, themes=themes,
                            cursor=cursor, limit=PAGE_LIMIT, cancelled=cancelled)
                if cancelled():
                    return FederatedResult(source.source_id, source.label, error="cancelled")
                items, next_cursor, fresh = extract(raw)
                # Safe even against an endless iterator supplied by a plugin.
                page = tuple(islice(iter(items), PAGE_LIMIT + 1))
                if len(page) > PAGE_LIMIT:
                    return FederatedResult(source.source_id, source.label,
                                           error="limit_exceeded")
                rows.extend(page)
                freshness = max(freshness, min(int(fresh), 7 * 86400))
                pages += 1
                if not next_cursor or not page:
                    cursor = next_cursor or None
                    break
                if next_cursor in seen:
                    return FederatedResult(source.source_id, source.label,
                                           error="cursor_loop")
                seen.add(next_cursor)
                cursor = next_cursor
            if cancelled():
                return FederatedResult(source.source_id, source.label, error="cancelled")
            return FederatedResult(
                source.source_id, source.label, tuple(rows),
                freshness, cursor or "", pages,
            )
        except Exception:
            # Never echo vendor errors, URLs, auth credentials or raw payloads.
            return FederatedResult(source.source_id, source.label, error="source_error")

    results: list[FederatedResult] = []
    # At most two concurrent network fetches. Do not mutate the Youth index
    # in these workers; on_source is a GTK idle scheduling hook.
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY, thread_name_prefix="youth-fed") as pool:
        pending = [pool.submit(collect, source) for source in chosen]
        for future in as_completed(pending):
            item = future.result()
            results.append(item)
            if on_source is not None and not cancelled():
                on_source(item)
    return FederatedReport(tuple(results), len(chosen), skipped, cancelled())
