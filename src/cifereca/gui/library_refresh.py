"""Identity-scoped Library and Recent snapshot refresh tasks.

MainWindow owns the active identity, pending flags and independent request
counters. These adapters submit only the existing service operations through
its TaskRunner; completion and GTK delivery stay on the runner's main thread.
The module is importable without GTK and starts no work on import.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..task_queue import TaskPriority

if TYPE_CHECKING:
    from gi.repository import Gtk


def refresh_library(self, scan: bool = False, control: Gtk.Widget | None = None, *, refresh_covers: bool = False) -> None:
    if self._library_refresh_pending and control is None:
        return
    self._library_refresh_pending = True
    self._library_refresh_generation = getattr(self, "_library_refresh_generation", 0) + 1
    request_generation = self._library_refresh_generation
    identity_id = self.service.current_identity.identity_id if self.service.current_identity else None
    generation = self._identity_context_generation
    def work() -> list[dict[str, Any]]:
        if refresh_covers:
            self.service.catalog_scan(force_covers=True)
        return self.service.library_entries(identity_id=identity_id, scan=scan and not refresh_covers)
    def ok(entries: list[dict[str, Any]]) -> None:
        if (getattr(self, "_closed", False) or
                request_generation != self._library_refresh_generation):
            return
        self._library_refresh_pending = False
        if not self._identity_request_current(identity_id, generation):
            self.refresh_library(False)
            return
        self._show_library_entries(entries)
    def err(exc: BaseException) -> None:
        if (getattr(self, "_closed", False) or
                request_generation != self._library_refresh_generation):
            return
        self._library_refresh_pending = False
        if not self._identity_request_current(identity_id, generation):
            self.refresh_library(False)
            return
        self.diagnostics.add("UI", "library refresh", "error", detail={"error": type(exc).__name__, "message": str(exc)})
        if control is not None:
            self.show_error(str(exc), "Actualisation de la bibliothèque impossible")
    self.runner.submit(
        "Actualisation de la bibliothèque…", work, ok, err, control=control,
        priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.NORMAL,
        kind="library",
    )


def refresh_recent(self, control: Gtk.Widget | None = None) -> None:
    if self._recent_refresh_pending and control is None:
        return
    self._recent_refresh_pending = True
    self._recent_refresh_generation = getattr(self, "_recent_refresh_generation", 0) + 1
    request_generation = self._recent_refresh_generation
    identity = self.service.current_identity
    identity_id = identity.identity_id if identity is not None else None
    generation = self._identity_context_generation
    def work() -> list[dict[str, Any]]:
        return self.service.recent_acquisitions(identity_id=identity_id)
    def ok(items: list[dict[str, Any]]) -> None:
        if (getattr(self, "_closed", False) or
                request_generation != self._recent_refresh_generation):
            return
        self._recent_refresh_pending = False
        if not self._identity_request_current(identity_id, generation):
            self.refresh_recent()
            return
        self._show_recent_entries(items)
    def err(exc: BaseException) -> None:
        if (getattr(self, "_closed", False) or
                request_generation != self._recent_refresh_generation):
            return
        self._recent_refresh_pending = False
        if not self._identity_request_current(identity_id, generation):
            self.refresh_recent()
            return
        self.diagnostics.add("UI", "recent refresh", "error", detail={"error": type(exc).__name__, "message": str(exc)})
        if control is not None:
            self.show_error(str(exc), "Actualisation des récupérations impossible")
    self.runner.submit(
        "Actualisation des récupérations récentes…", work, ok, err, control=control,
        priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.NORMAL,
        kind="recent",
    )
