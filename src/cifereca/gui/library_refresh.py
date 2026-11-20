"""Identity-scoped Library and Recent snapshot refresh tasks.

MainWindow owns the active identity, pending flags and independent request
counters. These adapters submit only the existing service operations through
its TaskRunner; completion and GTK delivery stay on the runner's main thread.
The module is importable without GTK and starts no work on import.
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from ..core.source_context import source_context_epoch
from ..task_queue import TaskPriority
from ..plugin_api import UserFacingError

if TYPE_CHECKING:
    from gi.repository import Gtk


_TRACE = logging.getLogger("cifereca")


def _trace_library_request(window, cause, decision):
    """Process-local counters only; no identity, account, path or coalesce key."""
    def counter(value):
        return value if type(value) is int and 0 <= value < 2**63 else -1

    try:
        epoch = source_context_epoch(window.service)
    except (AttributeError, ValueError, TypeError):
        epoch = -1
    _TRACE.info(
        "TRACE library refresh cause=%s decision=%s request_generation=%s "
        "identity_generation=%s scope_generation=%s source_epoch=%s mode=%s",
        cause, decision, counter(getattr(window, "_library_refresh_generation", 0)),
        counter(getattr(window, "_identity_context_generation", 0)),
        counter(getattr(window, "_library_scope_generation", 0)), counter(epoch),
        "all_profiles" if getattr(window, "_library_all_profiles", False) is True else "profile",
    )


def _library_scope(window):
    """Volatile navigation witness only; never a cache or acquisition grant."""
    service = window.service
    identity = service.current_identity
    try:
        epoch = source_context_epoch(service)
    except ValueError:
        return None
    return (service, epoch, getattr(identity, "identity_id", None),
            getattr(window, "_identity_context_generation", 0),
            getattr(window, "_library_scope_generation", 0),
            getattr(window, "_library_all_profiles", False) is True)


def _same_library_scope(left, right):
    return (left is not None and right is not None
            and left[0] is right[0] and left[1:] == right[1:])


def retire_outdated_library_snapshot(window):
    """Do not present previously consumed rows under a different read scope.

    Historical projections without a witness retain their existing lazy-load
    behaviour. The witness belongs to this window only, never to source rights.
    """
    loaded = getattr(window, "_library_loaded_scope", None)
    if loaded is not None and not _same_library_scope(loaded, _library_scope(window)):
        window._library_loaded_scope = None
        window._library_loaded_generation = -1
        window._library_entries_cache = []
        return True
    return False


def passive_library_refresh(window):
    """Only route navigation may join a pending same-scope snapshot.

    A generic refresh can be a publication notification and must retain its
    established dirty/follow-up semantics. An explicit click always submits.
    """
    if getattr(window, "_closed", False):
        return
    pending = getattr(window, "_library_pending_scope", None)
    if (getattr(window, "_library_refresh_pending", False)
            and _same_library_scope(pending, _library_scope(window))):
        _trace_library_request(window, "navigation", "join")
        return
    _trace_library_request(window, "navigation", "delegate")
    window.refresh_library(False)


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
        _trace_library_request(self, "publication", "follow_up")
        return
    _trace_library_request(self, "publication", "delegate")
    refresh = getattr(self, "refresh_library", None)
    if callable(refresh):
        refresh(False)


def refresh_library(self, scan: bool = False, control: Gtk.Widget | None = None, *, refresh_covers: bool = False) -> None:
    # A delayed request can be emitted after MainWindow's runner/service close.
    if getattr(self, "_closed", False):
        return
    scope = _library_scope(self)
    previous_scope = getattr(self, "_library_pending_scope", None)
    # A replacement Core cannot deliver the old pending callback. Admit a
    # separate read instead of leaving the new window waiting indefinitely.
    replaced = (previous_scope is not None and previous_scope[0] is not self.service)
    if self._library_refresh_pending and control is None and not replaced:
        # Coalesce background invalidations, but a newer explicit user
        # refresh already supersedes an ordinary redundant background call.
        # invalidate_library() still records real newly indexed artifacts.
        if not getattr(self, "_library_refresh_explicit_pending", False):
            self._library_refresh_dirty = True
            _trace_library_request(self, "automatic", "follow_up")
        else:
            _trace_library_request(self, "automatic", "join_explicit")
        return
    if control is not None or replaced:
        self._library_refresh_dirty = False
    self._library_refresh_pending = True
    self._library_pending_scope = scope
    self._library_refresh_explicit_pending = control is not None
    self._library_refresh_generation = getattr(self, "_library_refresh_generation", 0) + 1
    request_generation = self._library_refresh_generation
    _trace_library_request(self, "user" if control is not None else "automatic", "admitted")
    service = self.service
    selected = service.current_identity
    identity_id = selected.identity_id if selected is not None else None
    generation = self._identity_context_generation
    all_profiles = getattr(self, "_library_all_profiles", False) is True
    lookup = getattr(service, "identity_by_id", None)
    admitted_identity = lookup(identity_id) if callable(lookup) and identity_id else None

    def work() -> list[dict[str, Any]]:
        # Scans reconcile the configured local catalogue, not a selected
        # provider account. Navigation and superseding view requests are
        # publication guards only. A disposed/replaced service must not
        # perform a queued scan, and a removed identity cannot be reused.
        if (getattr(self, "_closed", False) or self.service is not service
                or type(identity_id) is not str or not identity_id
                or (callable(lookup) and (admitted_identity is None
                    or lookup(identity_id) is not admitted_identity))):
            return []
        if all_profiles:
            # Read every per-profile Core projection as independent owner
            # rows; never scan or call a supplier in this browse mode.
            from .library_global_projection import global_library_display_rows
            return global_library_display_rows(
                service.library_entries_all_profiles(),
                viewing_identity_id=identity_id,
            )
        if refresh_covers:
            service.catalog_scan(force_covers=True)
        return service.library_entries(identity_id=identity_id, scan=scan and not refresh_covers)
    def ok(entries: list[dict[str, Any]]) -> None:
        if (getattr(self, "_closed", False) or self.service is not service
                or request_generation != self._library_refresh_generation
                or not self._library_refresh_pending):
            return
        self._library_refresh_pending = False
        self._library_pending_scope = None
        self._library_refresh_explicit_pending = False
        if getattr(self, "_library_refresh_dirty", False):
            self._library_refresh_dirty = False
            self.refresh_library(False)
            return
        if (not self._identity_request_current(identity_id, generation)
                or (scope is not None and not _same_library_scope(scope, _library_scope(self)))):
            self.refresh_library(False)
            return
        self._show_library_entries(entries)
        # The same scoped snapshot drives the Library provider selector:
        # no provider I/O, no filesystem scan and no cross-profile hint.
        from .query_widget_factory_controller import refresh_library_origin_choices
        if all_profiles:
            refresh_library_origin_choices(
                self, entries, identity_id=identity_id, all_profiles=True,
            )
        else:
            # Retain the exact legacy identity-scoped call and its ABI.
            refresh_library_origin_choices(self, entries, identity_id=identity_id)
        # A successful *empty* response is still a loaded snapshot for the
        # selected identity. Do not resubmit work on every return to Library.
        self._library_loaded_generation = generation
        self._library_loaded_scope = scope
        _trace_library_request(self, "completion", "rendered")
    def err(exc: BaseException) -> None:
        if (getattr(self, "_closed", False) or self.service is not service
                or request_generation != self._library_refresh_generation
                or not self._library_refresh_pending):
            return
        self._library_refresh_pending = False
        self._library_pending_scope = None
        self._library_refresh_explicit_pending = False
        if getattr(self, "_library_refresh_dirty", False):
            self._library_refresh_dirty = False
            self.refresh_library(False)
            return
        if (not self._identity_request_current(identity_id, generation)
                or (scope is not None and not _same_library_scope(scope, _library_scope(self)))):
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
        # An unprompted background refresh can finish during navigation.
        # Callback failures belong to activity/diagnostics, not a modal.
        origin="user" if control is not None else "automatic",
    )


def refresh_recent(self, control: Gtk.Widget | None = None) -> None:
    if getattr(self, "_closed", False):
        return
    if self._recent_refresh_pending and control is None:
        return
    self._recent_refresh_pending = True
    self._recent_refresh_generation = getattr(self, "_recent_refresh_generation", 0) + 1
    request_generation = self._recent_refresh_generation
    service = self.service
    identity = service.current_identity
    identity_id = identity.identity_id if identity is not None else None
    generation = self._identity_context_generation
    def work() -> list[dict[str, Any]]:
        # This already-admitted recent/SQLite read is scoped to the captured
        # Core instance and identity. UI navigation or window teardown is
        # not source revocation; only GTK delivery belongs to those guards.
        # The Core read accepts an explicit identity and no supplier GET.
        return service.recent_acquisitions(identity_id=identity_id)
    def ok(items: list[dict[str, Any]]) -> None:
        if (getattr(self, "_closed", False) or self.service is not service or
                request_generation != self._recent_refresh_generation):
            return
        self._recent_refresh_pending = False
        if not self._identity_request_current(identity_id, generation):
            self.refresh_recent()
            return
        self._show_recent_entries(items)
        self._recent_loaded_generation = generation
    def err(exc: BaseException) -> None:
        if (getattr(self, "_closed", False) or self.service is not service or
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
        origin="user" if control is not None else "automatic",
    )
