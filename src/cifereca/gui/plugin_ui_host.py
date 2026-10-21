"""GTK adapter implementing the public plugin UI host contracts.

MainWindow owns identity, routes, listeners and widget lifetime. This adapter
only forwards public contributions to that owner; plugins consume plugin_api.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..plugin_api import BookRef
from .action_widgets import AdaptiveActionBar, action_button
from .public_book_view import PublicBookMasterDetailView

if TYPE_CHECKING:
    from .main_window import MainWindow


class _UiHost:
    def __init__(self, window: "MainWindow", action_context=None) -> None:
        self.window = window
        self._action_context = action_context
        self._performance_generation = getattr(window, "_shell_page_rebuild_token", None)
    def _window_alive(self) -> bool:
        return not getattr(self.window, "_closed", False)
    def toast(self, message: str, timeout: int = 3) -> None:
        # Toasts may be emitted by a plugin callback after MainWindow closes.
        if not self._window_alive():
            return
        self.window.toast(message, timeout)
    def _performance_monitor(self):
        from cifereca.plugin_api import UnsupportedOperation
        if (not self._window_alive()
                or getattr(self.window, "_shell_page_rebuild_token", None)
                is not self._performance_generation):
            raise UnsupportedOperation("Observation des performances indisponible.")
        monitor = getattr(self.window, "ui_perf", None)
        if (monitor is None
                or not callable(getattr(monitor, "performance_report", None))
                or not callable(getattr(monitor, "enable_detailed_capture", None))):
            raise UnsupportedOperation("Observation des performances indisponible.")
        return monitor

    def performance_report(self) -> dict[str, Any]:
        """Read the existing observer without enabling collection or I/O."""
        from cifereca.plugin_api import UnsupportedOperation
        monitor = self._performance_monitor()
        try:
            report = monitor.performance_report()
            if type(report) is not dict or report.get("schema") != "cifereca.performance.phases/1":
                raise ValueError("invalid_performance_report")
            return report
        except Exception:
            raise UnsupportedOperation("Rapport de performances indisponible.") from None

    def set_performance_capture(self, enabled: bool) -> dict[str, Any]:
        """An explicit, exact boolean; never a toggle on page construction."""
        from cifereca.plugin_api import UnsupportedOperation
        if type(enabled) is not bool:
            raise ValueError("invalid_performance_capture_option")
        monitor = self._performance_monitor()
        try:
            monitor.enable_detailed_capture(enabled)
        except Exception:
            raise UnsupportedOperation("Capture de performances indisponible.") from None
        return self.performance_report()

    def action_context(self):
        if self._action_context is None:
            from cifereca.plugin_api import UiActionContext
            route = self.window.view_stack.get_visible_child_name() if hasattr(self.window, "view_stack") else ""
            identity = getattr(getattr(self.window, "service", None), "current_identity", None)
            identity_id = getattr(identity, "identity_id", "")
            self._action_context = UiActionContext(
                target="page", route=route,
                identity_id=identity_id if isinstance(identity_id, str) else "",
            )
        return self._action_context
    def refresh_context(self) -> None:
        if not self._window_alive():
            return
        route = self.action_context().route
        if route in {"library", "recent"}:
            self.window.refresh_library(False)
            # The library refresh can synchronously close MainWindow.
            if self._window_alive():
                self.window.refresh_recent()
        elif route == "search":
            self.window.run_search(False)
    def current_book(self):
        return self.action_context().book
    def detail_sections(self):
        return ()
    def refresh_detail(self) -> None:
        self.refresh_context()
    def open_plugin_settings(self, plugin_id: str) -> None:
        if not self._window_alive():
            return
        self.window.navigate("plugins")
        if not self._window_alive():
            return
        # Reentrant navigation may have selected another page. Do not focus
        # controls belonging to an inactive (or detached) plugin manager.
        stack = getattr(self.window, "view_stack", None)
        if stack is not None and stack.get_visible_child_name() != "plugins":
            return
        page = getattr(self.window, "plugins_page", None)
        opener = getattr(page, "focus_plugin", None)
        if callable(opener):
            opener(plugin_id)
    def navigate(self, route: str) -> None:
        if self._window_alive():
            self.window.navigate(route)
    def open_book_detail(self, book: BookRef, *, identity_id: str | None = None) -> bool:
        if not self._window_alive():
            return False
        return self.window.open_book_detail(book, identity_id=identity_id)
    def search_books(
        self, query: str, *, field: str = "title",
        online: bool = False, identity_id: str | None = None,
        providers: tuple[str, ...] = (),
    ) -> bool:
        if not self._window_alive():
            return False
        # Preserve the legacy public host contract for the default route:
        # existing GTK harnesses and plugins accept no providers keyword.
        # Only a caller explicitly narrowing the scope uses the extension.
        if providers:
            return self.window.search_books(
                query, field=field, online=online, identity_id=identity_id,
                providers=providers,
            )
        return self.window.search_books(
            query, field=field, online=online, identity_id=identity_id,
        )
    def can_open_loaded_search_notice(
        self, *, provider: str, credential: str, record_id: str,
        docbase: str, source_collection: str, identity_id: str,
    ) -> bool:
        if not self._window_alive():
            return False
        return self.window.can_open_loaded_search_notice(
            provider=provider, credential=credential, record_id=record_id,
            docbase=docbase, source_collection=source_collection,
            identity_id=identity_id,
        )

    def open_loaded_search_notice(
        self, *, provider: str, credential: str, record_id: str,
        docbase: str, source_collection: str, identity_id: str,
    ) -> bool:
        if not self._window_alive():
            return False
        return self.window.open_loaded_search_notice(
            provider=provider, credential=credential, record_id=record_id,
            docbase=docbase, source_collection=source_collection,
            identity_id=identity_id,
        )

    def build_action_bar(self, actions):
        bar = AdaptiveActionBar()
        generation = getattr(self.window, "_shell_page_rebuild_token", None)
        for action in actions:
            button = action_button(action.title, action.icon_name)
            # A detached plugin page may outlive its source registration and
            # dispatch a queued GTK click after the shell has been rebuilt.
            button.connect('clicked', lambda _button, contribution=action: (
                contribution.callback(self)
                if self._window_alive()
                and getattr(self.window, "_shell_page_rebuild_token", None) is generation
                else None
            ))
            bar.append(button)
        return bar
    def build_book_master_detail(self, *, title: str, subtitle: str = "", empty_title: str = "Aucun document", empty_desc: str = "Aucun document à afficher."):
        return PublicBookMasterDetailView(
            self.window, title=title, subtitle=subtitle,
            empty_title=empty_title, empty_desc=empty_desc,
        )
    def build_standard_page(self, route: str):
        return self.window._build_standard_page(route)
    def import_acsm(self):
        if self._window_alive():
            self.window.choose_acsm()
    def identity_overview(self) -> list[dict[str, Any]]:
        if not self._window_alive():
            return []
        from copy import deepcopy
        return deepcopy(self.window.service.dashboard_state())
    def open_identity_library(self, identity_id: str) -> None:
        if self._window_alive():
            self.window.open_identity_library(identity_id)
    def supports_identity_view_filters(self) -> bool:
        return True
    def open_identity_view(self, identity_id: str, *, route: str, providers=()) -> None:
        if self._window_alive():
            self.window.open_identity_view(identity_id, route=route, providers=providers)
    def synchronize(self, *, deep: bool = False, control=None) -> None:
        if self._window_alive():
            self.window.run_opening_sync(control, deep=deep)
    def subscribe_identity_overview(self, callback):
        # Do not register a new owner-bound observer once teardown has started.
        if not self._window_alive():
            return lambda: None
        listeners = getattr(self.window, "_overview_listeners", None)
        if listeners is None:
            listeners = []; self.window._overview_listeners = listeners
        listeners.append(callback)
        def cancel():
            # Equality is not subscription identity: callable objects may
            # compare equal while representing distinct plugin subscriptions.
            for index, listener in enumerate(listeners):
                if listener is callback:
                    del listeners[index]
                    break
        return cancel
    def plugin_statuses(self) -> list[dict[str, Any]]:
        if not self._window_alive():
            return []
        manager = self.window.plugin_manager
        return manager.diagnostic() if manager is not None else []
