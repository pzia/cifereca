"""GTK-free dispatcher; the shell owns selection, visibility and widget lifetime."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import weakref

from cifereca.plugin_api.page_lifecycle import PageLifecycleV1, PageViewContext


@dataclass
class _Page:
    hooks: PageLifecycleV1
    identity: str | None = None
    account: tuple[str, str, str] | None = None
    generation: int = 0
    dirty: bool = True
    failed: bool = False


class PageLifecycleDispatcher:
    def __init__(self, alive: Callable[[], bool], error: Callable[[str], None]):
        self._alive = alive
        self._error = error
        self._pages: dict[str, _Page] = {}
        self._identity = None
        self._account = None
        self._route = None
        self._generation = 0
        self._depth = 0
        self._disposed = False

    def _valid(self):
        return not self._disposed and self._alive()

    def handles(self, route):
        return route in self._pages and self._valid()

    def _advance(self, page):
        self._generation += 1
        page.generation = self._generation

    def _event(self, route, page, reason):
        owner = weakref.ref(self)
        generation = page.generation

        def current(visible):
            dispatcher = owner()
            if dispatcher is None or not dispatcher._valid():
                return False
            candidate = dispatcher._pages.get(route)
            return (candidate is not None and candidate.generation == generation
                    and not candidate.failed
                    and (not visible or dispatcher._route == route))

        return PageViewContext(route, page.identity, page.account, generation, reason, current)

    def _report(self, code):
        try:
            self._error(code)
        except Exception:
            pass

    def _notify_context(self, route, page, reason):
        event = self._event(route, page, reason)
        if not event.is_current(require_visible=False):
            return
        try:
            page.hooks.on_context_changed(event)
        except Exception:
            if event.is_current(require_visible=False):
                page.failed = True
                self._report("page_context_failed")

    def mount(self, route, widget):
        if not self._valid():
            return False
        try:
            hooks = getattr(widget, "cifereca_page_lifecycle_v1", None)
        except Exception:
            self._report("page_lifecycle_invalid")
            return False
        if type(hooks) is not PageLifecycleV1:
            return False
        if route in self._pages:
            self.detach(route)
        if not self._valid():
            return False
        page = _Page(hooks, self._identity, self._account if hooks.account_scoped else None)
        self._advance(page)
        self._pages[route] = page
        self._depth += 1
        try:
            self._notify_context(route, page, "mounted")
        finally:
            self._depth -= 1
        self._flush()
        return True

    def context_changed(self, identity, account):
        if not self._valid() or (identity, account) == (self._identity, self._account):
            return
        self._identity, self._account = identity, account
        pending = []
        # Invalidate every affected generation before calling any plugin.
        for route, page in tuple(self._pages.items()):
            target_account = account if page.hooks.account_scoped else None
            if (page.identity, page.account) == (identity, target_account):
                continue
            reason = "identity" if page.identity != identity else "account"
            page.identity, page.account = identity, target_account
            self._advance(page)
            page.dirty, page.failed = True, False
            pending.append((route, page, page.generation, reason))
        self._depth += 1
        try:
            for route, page, generation, reason in pending:
                if (self._valid() and self._pages.get(route) is page
                        and page.generation == generation):
                    self._notify_context(route, page, reason)
        finally:
            self._depth -= 1
        self._flush()

    def show(self, route):
        if not self._valid():
            return
        if route != self._route:
            previous = self._pages.get(self._route)
            if previous is not None:
                self._advance(previous)
                previous.dirty = True
            self._route = route
        self._flush()

    def _flush(self):
        if self._depth or not self._valid():
            return
        route = self._route
        page = self._pages.get(route)
        if page is None or page.failed or not page.dirty:
            return
        page.dirty = False
        self._advance(page)
        event = self._event(route, page, "show")
        self._depth += 1
        try:
            page.hooks.on_show(event)
        except Exception:
            if event.is_current(require_visible=False):
                page.failed = True
                self._report("page_show_failed")
        finally:
            self._depth -= 1
        # A synchronous navigation/context transition inside the hook may
        # have marked a different mounted generation dirty. Do not recurse
        # through unbounded plugin redirects; the next shell event flushes it.

    def detach(self, route):
        page = self._pages.pop(route, None)
        if self._route == route:
            self._route = None
        if page is not None and callable(page.hooks.on_dispose):
            try:
                page.hooks.on_dispose()
            except Exception:
                self._report("page_dispose_failed")

    def dispose(self):
        if self._disposed:
            return
        # Invalidate all handles before notifying the first detached page.
        self._disposed = True
        pages, self._pages = self._pages, {}
        self._route = None
        for page in pages.values():
            if callable(page.hooks.on_dispose):
                try:
                    page.hooks.on_dispose()
                except Exception:
                    self._report("page_dispose_failed")


def _coordinates(window):
    service = getattr(window, "service", None)
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    identity = identity if type(identity) is str and identity else None
    account = getattr(service, "current_account", None)
    scope = tuple(getattr(account, key, None) for key in ("provider_id", "credential_id", "unique_id"))
    if identity is None or not all(type(value) is str and value for value in scope):
        scope = None
    return identity, scope


def reset(window):
    old = getattr(window, "_page_lifecycle_dispatcher", None)

    def alive():
        host = owner()
        return host is not None and not getattr(host, "_closed", False)

    def error(code):
        host = owner()
        diagnostics = getattr(host, "diagnostics", None)
        if diagnostics is not None:
            diagnostics.add("PLUGIN", "ui.page lifecycle", "error", detail={"code": code})

    try:
        owner = weakref.ref(window)
    except TypeError:
        owner = lambda: window  # lightweight non-GTK test hosts
    dispatcher = PageLifecycleDispatcher(alive, error)
    window._page_lifecycle_dispatcher = dispatcher
    if old is not None:
        old.dispose()
    if alive() and getattr(window, "_page_lifecycle_dispatcher", None) is dispatcher:
        dispatcher.context_changed(*_coordinates(window))
    return dispatcher


def context_changed(window):
    dispatcher = getattr(window, "_page_lifecycle_dispatcher", None)
    if dispatcher is not None and not getattr(window, "_closed", False):
        dispatcher.context_changed(*_coordinates(window))


def handles(window, route):
    dispatcher = getattr(window, "_page_lifecycle_dispatcher", None)
    return dispatcher is not None and dispatcher.handles(route)
