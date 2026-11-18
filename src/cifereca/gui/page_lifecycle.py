"""Shell-owned presentation notifications; importable without GTK (#4010).

This coordinator deliberately has no worker, network, plugin activation or
business broker. The event lease only governs presentation and new UI actions;
it never cancels an already admitted business operation.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..plugin_api.models import UiPageLifecycleEvent


@dataclass(frozen=True, slots=True)
class PageScope:
    owner: int
    identity: str | None
    identity_epoch: int
    account: tuple[str, str, str] | None
    account_epoch: int

    def key(self, account_scoped):
        identity = (self.owner, self.identity, self.identity_epoch)
        return identity + (self.account, self.account_epoch) if account_scoped else identity


@dataclass(slots=True)
class _Mounted:
    route: str
    widget: object
    host: object
    callback: object
    account_scoped: bool
    generation: int = 0


class PageLifecycles:
    def __init__(self, read_scope, alive, report_failure):
        self._read_scope = read_scope
        self._alive = alive
        self._report_failure = report_failure
        self._scope = read_scope()
        self._entries = {}
        self._visible = None
        self._counter = 0

    def mount(self, page, widget, host):
        callback = getattr(page, "lifecycle", None)
        if not callable(callback):
            return
        self._entries[page.route] = _Mounted(
            page.route, widget, host, callback,
            getattr(page, "lifecycle_account_scoped", False) is True,
        )

    def manages(self, route):
        return route in self._entries and self._alive()

    def _prepare(self, entry, reason):
        self._counter += 1
        entry.generation = self._counter
        generation = entry.generation
        scope = self._scope
        visible = self._visible == entry.route

        def valid_stamp():
            return (
                self._alive()
                and self._entries.get(entry.route) is entry
                and entry.generation == generation
            )

        def current():
            try:
                return (
                    visible and valid_stamp() and self._visible == entry.route
                    and self._read_scope().key(entry.account_scoped)
                    == scope.key(entry.account_scoped)
                )
            except Exception:
                return False

        return entry, UiPageLifecycleEvent(
            route=entry.route, identity_id=scope.identity,
            account=scope.account if entry.account_scoped else None,
            generation=generation, reason=reason, visible=visible,
            _current=current,
        ), valid_stamp

    def _call(self, entry, event):
        try:
            entry.callback(entry.host, entry.widget, event)
        except Exception:
            try:
                self._report_failure()
            except Exception:
                pass

    def _deliver(self, pending):
        # All affected records are invalidated before calling the first
        # plugin. Nested navigation/rebuild can supersede the remaining hooks.
        for entry, event, valid_stamp in pending:
            if valid_stamp():
                self._call(entry, event)

    def _update(self, route):
        if not self._alive():
            return
        old_scope, outgoing = self._scope, self._visible
        self._scope, self._visible = self._read_scope(), route
        pending = []
        for entry in tuple(self._entries.values()):
            reason = None
            if outgoing != route and entry.route == outgoing:
                reason = "hide"
            elif outgoing != route and entry.route == route:
                reason = "show"
            elif old_scope.key(entry.account_scoped) != self._scope.key(entry.account_scoped):
                reason = "context_changed"
            if reason is not None:
                pending.append(self._prepare(entry, reason))
        self._deliver(pending)

    def context_changed(self):
        self._update(self._visible)

    def show(self, route):
        self._update(route)

    def dispose(self):
        entries = tuple(self._entries.values())
        self._entries.clear()
        self._visible = None
        for entry in entries:
            _, event, _ = self._prepare(entry, "dispose")
            self._call(entry, event)


def _window_scope(window):
    service = getattr(window, "service", None)
    identity = getattr(service, "current_identity", None)
    identity_id = getattr(identity, "identity_id", None)
    if type(identity_id) is not str:
        identity_id = None
    account = getattr(service, "current_account", None)
    coordinates = None
    if (identity_id is not None and account is not None
            and getattr(account, "identity_id", identity_id) == identity_id):
        candidate = tuple(getattr(account, key, None)
                          for key in ("provider_id", "credential_id", "unique_id"))
        if all(type(value) is str for value in candidate):
            coordinates = candidate
    return PageScope(
        id(service), identity_id,
        getattr(window, "_identity_context_generation", 0),
        coordinates, getattr(window, "_account_context_generation", 0),
    )


def renew_lifecycles(window):
    old = getattr(window, "_page_lifecycles", None)

    def alive():
        return (not getattr(window, "_closed", False)
                and getattr(window, "_page_lifecycles", None) is owner)

    def failure():
        window.diagnostics.add("PLUGIN", "ui.page lifecycle", "error",
                               detail={"code": "page_lifecycle_failed"})

    owner = PageLifecycles(lambda: _window_scope(window), alive, failure)
    # Publish the replacement before invoking dispose callbacks: a recursive
    # rebuild owns its own generation and must never be overwritten here.
    window._page_lifecycles = owner
    if old is not None:
        old.dispose()
    return owner


def notify_context(window):
    owner = getattr(window, "_page_lifecycles", None)
    if owner is not None:
        owner.context_changed()


def notify_visible(window, route):
    owner = getattr(window, "_page_lifecycles", None)
    if owner is not None:
        owner.show(route)


def manages_route(window, route):
    owner = getattr(window, "_page_lifecycles", None)
    return owner is not None and owner.manages(route)
