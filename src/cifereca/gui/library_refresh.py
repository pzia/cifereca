"""Identity-scoped Library and Recent snapshot refresh tasks.

MainWindow owns the active identity, pending flags and independent request
counters. These adapters submit only the existing service operations through
its TaskRunner; completion and GTK delivery stay on the runner's main thread.
The module is importable without GTK and starts no work on import.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..task_queue import TaskPriority
from ..plugin_api import UserFacingError

if TYPE_CHECKING:
    from gi.repository import Gtk


def invalidate_library(self) -> None:
    """Signal newly indexed bytes, unlike ordinary coalesced background refresh.

    The existing user-triggered refresh retains priority. Its already-read
    snapshot might predate the acquisition, so re-read once on GTK completion.
    This function is only called from TaskRunner's GTK completion callbacks.
    """
    if getattr(self, "_closed", False):
        return
    from .search_snapshot_refresh import invalidate_search_snapshot
    invalidate_search_snapshot(self)
    if getattr(self, "_library_refresh_pending", False):
        self._library_refresh_dirty = True
        return
    refresh = getattr(self, "refresh_library", None)
    if callable(refresh):
        refresh(False)


def refresh_library(self, scan: bool = False, control: Gtk.Widget | None = None, *, refresh_covers: bool = False) -> None:
    # A delayed request can be emitted after MainWindow's runner/service close.
    if getattr(self, "_closed", False):
        return
    if self._library_refresh_pending and control is None:
        # Coalesce background invalidations, but a newer explicit user
        # refresh already supersedes an ordinary redundant background call.
        # invalidate_library() still records real newly indexed artifacts.
        if not getattr(self, "_library_refresh_explicit_pending", False):
            self._library_refresh_dirty = True
        return
    if control is not None:
        self._library_refresh_dirty = False
    self._library_refresh_pending = True
    self._library_refresh_explicit_pending = control is not None
    self._library_refresh_generation = getattr(self, "_library_refresh_generation", 0) + 1
    request_generation = self._library_refresh_generation
    identity_id = self.service.current_identity.identity_id if self.service.current_identity else None
    generation = self._identity_context_generation
    def work() -> list[dict[str, Any]]:
        # A request can remain queued while navigation switches identity or
        # a newer manual refresh supersedes it. Avoid expensive filesystem
        # scans or service queries for a context whose GTK result must be
        # discarded anyway (#981). In-flight work is not interrupted.
        if (getattr(self, "_closed", False)
                or request_generation != self._library_refresh_generation
                or not self._identity_request_current(identity_id, generation)):
            return []
        if refresh_covers:
            self.service.catalog_scan(force_covers=True)
        return self.service.library_entries(identity_id=identity_id, scan=scan and not refresh_covers)
    def ok(entries: list[dict[str, Any]]) -> None:
        if (getattr(self, "_closed", False) or
                request_generation != self._library_refresh_generation):
            return
        self._library_refresh_pending = False
        self._library_refresh_explicit_pending = False
        if getattr(self, "_library_refresh_dirty", False):
            self._library_refresh_dirty = False
            self.refresh_library(False)
            return
        if not self._identity_request_current(identity_id, generation):
            self.refresh_library(False)
            return
        self._show_library_entries(entries)
        # The same scoped snapshot drives the Library provider selector:
        # no provider I/O, no filesystem scan and no cross-profile hint.
        from .query_widget_factory_controller import refresh_library_origin_choices
        refresh_library_origin_choices(self, entries, identity_id=identity_id)
        # A successful *empty* response is still a loaded snapshot for the
        # selected identity. Do not resubmit work on every return to Library.
        self._library_loaded_generation = generation
    def err(exc: BaseException) -> None:
        if (getattr(self, "_closed", False) or
                request_generation != self._library_refresh_generation):
            return
        self._library_refresh_pending = False
        self._library_refresh_explicit_pending = False
        if getattr(self, "_library_refresh_dirty", False):
            self._library_refresh_dirty = False
            self.refresh_library(False)
            return
        if not self._identity_request_current(identity_id, generation):
            self.refresh_library(False)
            return
        self.diagnostics.add("UI", "library refresh", "error", detail={"code": "library_refresh_failed"})
        if control is not None:
            self.show_error(
                exc.user_message if isinstance(exc, UserFacingError) else "Traitement indisponible",
                "Actualisation de la bibliothèque impossible",
            )
    self.runner.submit(
        "Actualisation de la bibliothèque…", work, ok, err, control=control,
        priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.NORMAL,
        kind="library",
    )


def refresh_recent(self, control: Gtk.Widget | None = None) -> None:
    if getattr(self, "_closed", False):
        return
    if self._recent_refresh_pending and control is None:
        return
    self._recent_refresh_pending = True
    self._recent_refresh_generation = getattr(self, "_recent_refresh_generation", 0) + 1
    request_generation = self._recent_refresh_generation
    identity = self.service.current_identity
    identity_id = identity.identity_id if identity is not None else None
    generation = self._identity_context_generation
    def work() -> list[dict[str, Any]]:
        # No service fetch for a queued request invalidated by a newer
        # request, profile switch or MainWindow close (#981).
        if (getattr(self, "_closed", False)
                or request_generation != self._recent_refresh_generation
                or not self._identity_request_current(identity_id, generation)):
            return []
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
        self._recent_loaded_generation = generation
    def err(exc: BaseException) -> None:
        if (getattr(self, "_closed", False) or
                request_generation != self._recent_refresh_generation):
            return
        self._recent_refresh_pending = False
        if not self._identity_request_current(identity_id, generation):
            self.refresh_recent()
            return
        self.diagnostics.add("UI", "recent refresh", "error", detail={"code": "recent_refresh_failed"})
        if control is not None:
            self.show_error(
                exc.user_message if isinstance(exc, UserFacingError) else "Traitement indisponible",
                "Actualisation des récupérations impossible",
            )
    self.runner.submit(
        "Actualisation des récupérations récentes…", work, ok, err, control=control,
        priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.NORMAL,
        kind="recent",
    )
