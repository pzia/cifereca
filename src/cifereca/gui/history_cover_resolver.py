"""Lazy history-cover lookup over an explicit service and diagnostics contract.

The GUI owner retains cancellation holders and main-loop callback delivery;
no MainWindow import or alternate cover loader is introduced.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib

from ..diagnostics import Diagnostics
from ..services import LibraryService


class _MetaRequest:
    def __init__(self, holder: dict[str, bool]):
        self.holder = holder

    def cancel(self) -> None:
        self.holder["active"] = False


class HistoryCoverResolver:
    """Résolution lazy des couvertures absentes de l'historique lié.

    La 0.2.6 n'utilise plus les RecordId ListHandings pour GetListHoldings : les RecordId observés sur un
    profil lié provoquent systématiquement une erreur Syracuse. On recherche
    donc le document par titre/auteur/année uniquement pour les cellules
    réellement matérialisées, avec deux workers et déduplication.
    """

    def __init__(self, service: LibraryService, diagnostics: Diagnostics):
        self.service = service
        self.diagnostics = diagnostics
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cifereca-history-cover")
        self._lock = threading.Lock()
        self._closed = False
        self._pending: dict[str, list[tuple[dict[str, bool], Callable[[str | None], None]]]] = {}
        self._memory: dict[str, tuple[str | None, str]] = {}
        self._stats = {
            "requested": 0,
            "memory_hit": 0,
            "matched": 0,
            "ambiguous": 0,
            "not_found": 0,
            "no_cover": 0,
            "error": 0,
            "completed": 0,
        }

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _deliver(self, holder, callback, value) -> bool:
        # Cancellation may happen after the worker queued this idle callback.
        with self._lock:
            active = not self._closed and holder.get("active", False)
        # Native admissions carry a passive source fence. Never issue a
        # provider/SQLite call on GTK merely to deliver a result.
        current = holder.get("source_current")
        if active and (current is None or current()):
            callback(value)
        return False

    def stats(self) -> dict[str, int]:
        with self._lock:
            out = dict(self._stats)
            out["pending"] = len(self._pending)
        out["workers"] = 2
        return out

    @staticmethod
    def _key(provider: str, record_id: str | None, title: str, author: str | None, year: str | None, scope: str = "") -> str:
        return "\0".join([
            scope, str(provider or ""), str(record_id or ""), str(title or ""),
            str(author or ""), str(year or ""),
        ])

    def request(
        self,
        provider: str,
        record_id: str | None,
        title: str,
        author: str | None,
        year: str | None,
        default_url: str | None,
        callback: Callable[[str | None], None], *, scope: str = "",
    ) -> _MetaRequest:
        holder = {"active": True}
        request = _MetaRequest(holder)
        with self._lock:
            if self._closed:
                holder["active"] = False
                return request
        title = str(title or "").strip()
        if not title:
            GLib.idle_add(self._deliver, holder, callback, default_url)
            return request
        # A scoped window cannot reuse another identity's in-memory result.
        # Existing callers without a scope keep compatibility.
        if type(scope) is not str or len(scope) > 128 or not scope.isprintable():
            scope = ""
        # Real Core callers must capture the borrower *before* the executor
        # runs. Legacy embedded service doubles retain the old lookup ABI.
        from cifereca.services import LibraryService
        native = isinstance(self.service, LibraryService)
        if native:
            from cifereca.core.library_projection import capture_history_cover_lookup
            admitted = capture_history_cover_lookup(
                self.service, self.service.current_account,
                provider=provider, record_id=record_id, title=title,
                author=author, year=year, default_url=default_url,
            )
            if admitted is None:
                GLib.idle_add(self._deliver, holder, callback, None)
                return request
            admitted_key, work, source_current = admitted
            holder["source_current"] = source_current
            scope = scope + ":" + admitted_key
        else:
            work = self.service.resolve_history_thumbnail
        key = self._key(provider, record_id, title, author, year, scope)
        with self._lock:
            self._stats["requested"] += 1
            # Native cache hits must still pass the durable borrower lease
            # on a worker. Avoid replaying an old private URL from GTK memory.
            if not native and key in self._memory:
                self._stats["memory_hit"] += 1
                value, _status = self._memory[key]
                GLib.idle_add(self._deliver, holder, callback, value or default_url)
                return request
            callbacks = self._pending.setdefault(key, [])
            callbacks.append((holder, callback))
            if len(callbacks) > 1:
                return request

        future = (self.pool.submit(work) if native else self.pool.submit(
            work, provider, record_id, title, author, year, default_url,
        ))

        def done(fut) -> None:
            try:
                value, status = fut.result()
            except Exception:
                value, status = (None if native else default_url), "error"
            with self._lock:
                if not native:
                    self._memory[key] = (value, status)
                    if len(self._memory) > 2048:
                        self._memory.pop(next(iter(self._memory)))
                callbacks = self._pending.pop(key, [])
                self._stats[status if status in {"matched", "ambiguous", "not_found", "no_cover", "error"} else "error"] += 1
                self._stats["completed"] += 1
                snapshot = dict(self._stats)
                snapshot["pending"] = len(self._pending)
                should_log = self._stats["completed"] == 1 or self._stats["completed"] % 10 == 0
            if should_log:
                snapshot["workers"] = 2
                self.diagnostics.add(
                    "COVER", "history search pipeline", "ok",
                    detail=snapshot,
                    level="detailed",
                )
            for state, cb in callbacks:
                if state.get("active"):
                    GLib.idle_add(self._deliver, state, cb, value)

        future.add_done_callback(done)
        return request

