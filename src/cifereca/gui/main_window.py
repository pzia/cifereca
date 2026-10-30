from __future__ import annotations

import re
import logging
import time
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import __version__
from ..catalogue_link import safe_document_link_url
from ..config import Config
from ..core.search import file_format
from ..identity_selection import identity_change_required
from ..language import language_label
from ..diagnostics import Diagnostics
from ..descriptions import description_text
from ..epub_metadata import notice_rows
from ..book_detail_metadata import detail_metadata_items, canonical_book_isbn, safe_detail_value
from ..models import Booking, HistoryItem, Holding, ItemDetail, Loan, SearchResult
from ..services import LibraryService
from .cover_loader import CoverLoader
from .book_widgets import BookListCell, BookListItem, CoverView, attribute_tag
from .book_actions import identity_has_attested_possession
from .booking_view import booking_presentation
from .layout import MASTER_DETAIL_NARROW_WINDOW_WIDTH, master_detail_is_narrow
from . import route_display_controller
from . import public_catalogue_action_controller
from . import recommendation_target_controller
from . import secondary_results_controller
from . import acsm_import_chooser_controller
from . import shell_stylesheet_controller
from . import settings_chooser_controller
from . import query_widget_factory_controller
from . import history_language_projection
from . import known_languages_controller
from . import adept_loan_choice_controller
from . import sync_issues_dialog_controller
from . import facet_value_controller
from . import shell_shortcut_controller
from . import identity_activation_controller
from . import identity_view_controller
from . import secondary_page_controller
from . import statistics_presentation_controller
from . import dashboard_page_controller
from . import dashboard_render_controller
from . import plugin_action_dispatch_controller
from . import shell_identity_selector_controller
from . import shell_account_selection_controller
from . import shell_layout_controller
from . import shell_body_controller
from . import plugin_book_action_controller, book_action_context_controller, loans_controller, sync_controller, search_results, search_details, search_controller, library_refresh, navigation_controller, task_activity_adapter, book_notice_controller, book_recommendation_controller, list_facet_controller, shell_page_controller, library_navigation, detail_facets_controller, header_action_controller, plugin_reload_controller, library_render_controller, recent_detail_controller, library_detail_controller, library_page_controller, local_book_action_controller
from . import shell_header_controller
from .search_state import SearchStateField
from .settings_lifetime import SettingsPageLifetime
from . import settings_page, standalone_settings_controller
from .window_close import close_window_resources
from .startup_page import build_startup_page
from .timeout_policy import bounded_seconds
from .startup_lifecycle import once_while_open
from . import startup_coordinator
from . import feedback_timers
from .notification_widgets import build_notification_bar
from . import activity_result_controller
from . import activity_feedback_view_controller
from . import feedback_dialog_controller
from .loan_view import (
    LOAN_FILTER_LABELS,
    loan_context_label,
    loan_due_presentation,
    loan_medium_counts,
)
from ..core.loan_source_identity import loan_source_key, unique_library_loan
from .recent_time import format_recent_timestamp
from .library_rows import project_library_rows
from .recent_rows import project_recent_rows
from .sync_view import sync_identity_action_label, sync_source_presentation
from .task_runner import TaskPriority, TaskRunner
from .maintenance_result_view import MaintenanceResultView
from .maintenance_tools import preview_maintenance_tool, execute_maintenance_tool, MaintenanceCancelControl, MaintenanceCancelledError, MaintenanceBusyError, MaintenancePlanChangedError, BUSY_MESSAGE, CHANGED_MESSAGE
from .maintenance_line_widgets import build_maintenance_line
from .return_feedback import loan_return_feedback
from .ui_perf import UiPerfMonitor
from .activity_feedback import ActivityFeedback
from .activity_history_projection import activity_history_rows
from .activity_hide_policy import can_reduce_activity
from .plugin_manager_page import PluginManagerPage
from .query_controls import QueryControls, MultiSelect
from .action_widgets import AdaptiveActionBar
from .query_model import result_row, online_search, progressive_online_search, merge_results
from .shell_model import resolve_pages, visible_actions, page_actions, delegated_accounts, menu_sections, contextual_actions, validate_action_selection
from ..plugin_api import (
    ArtifactFormat, ArtifactRef, ArtifactRouteRequest, BookReferenceRecommendationService, DetailField, DetailSection, DocumentLink, book_ref_from_row,
    MaintenanceContext, SyncRequest, UiActionContext, UiBookDetailPresentation, UiPageContribution, document_links,
    collect_cached_book_details, subscribe_cached_book_detail_changes,
)

_TRACE_LOG = logging.getLogger("cifereca")


from .detail_model import fr_date, text_or_dash, detail_display_value, detail_language, compact_availability_label, compact_detail_rows, detail_subtitle_lines, book_detail_presentation

from .action_widgets import clear_box, add_css, action_button, mirror_sensitivity


# Historical imports remain compatible during GUI extraction.
from .history_cover_resolver import HistoryCoverResolver, _MetaRequest


from .detail_panel import DetailPanel

from .master_detail import MasterDetailPage


from .public_book_view import PublicBookMasterDetailView
from .plugin_ui_host import _UiHost




class MainWindow(Adw.ApplicationWindow):
    # Stable facade names delegate to a per-window, GTK-free state container.
    _selected_search_id = SearchStateField("selected_id")
    _search_detail_request_generation = SearchStateField("detail_generation")
    _last_search_query = SearchStateField("last_query")
    _search_mode = SearchStateField("mode")
    _search_local_objects = SearchStateField("local_objects")
    search_objects = SearchStateField("objects")
    _remote_search_results = SearchStateField("remote_results")
    _search_generation = SearchStateField("generation")
    _remote_query_key = SearchStateField("remote_query_key")
    _online_pending_key = SearchStateField("online_pending_key")
    _online_pending_token = SearchStateField("online_pending_token")
    _online_pending_generation = SearchStateField("online_pending_generation")
    _search_enter_sequence = SearchStateField("enter_sequence")

    NAV_ITEMS = [
        ("dashboard", "Accueil", "view-dashboard-symbolic"),
        ("search", "Recherche", "system-search-symbolic"),
        ("library", "Bibliothèque", "accessories-dictionary-symbolic"),
        ("recent", "Récents", "document-open-recent-symbolic"),
        ("loans", "Emprunts", "folder-documents-symbolic"),
        ("plugins", "Plugins", "application-x-addon-symbolic"),
    ]

    def __init__(self, app: Adw.Application, config: Config, service: LibraryService, diagnostics: Diagnostics):
        super().__init__(application=app)
        self.config = config
        self.service = service
        self.diagnostics = diagnostics
        self.plugin_manager = getattr(service, "plugin_manager", None)
        self._plugin_page_widgets: dict[str, Gtk.Widget] = {}
        self._closed = False
        self._settings_main_lifetime: SettingsPageLifetime | None = None
        self._settings_standalone_lifetime: SettingsPageLifetime | None = None
        self._account_changing = False
        self._identity_changing = False
        self._identity_context_generation = 0
        self._account_context_generation = 0
        self._cached_book_request_generation = 0
        self._cached_book_context = None
        self._cached_book_services = ()
        self._cached_book_unsubscribe = lambda: None
        self._selected_search_id: str | None = None
        self._search_detail_request_generation = 0
        self._last_search_query = ""
        self._search_mode = "local"
        self._loaded_search_context = None
        self._search_local_objects: dict[str, dict[str, Any]] = {}
        self._kobo_backup_timer_id: int | None = None
        self.main_shell: Gtk.Widget | None = None
        self.loan_objects: dict[str, Loan] = {}
        self.history_objects: dict[str, HistoryItem] = {}
        self._history_rows: list[HistoryItem] = []
        self._history_loaded_count = 0
        self._history_page_size = 30
        self._history_append_scheduled = False
        self.search_objects: dict[str, SearchResult] = {}
        self.booking_objects: dict[str, Booking] = {}
        self._library_entries_cache: list[dict[str, Any]] = []
        self._recent_entries_cache: list[dict[str, Any]] = []
        self._library_refresh_pending = False
        self._recent_refresh_pending = False
        self._active_tasks: dict[int, tuple[int, str, str]] = {}
        self._finished_task_ids: set[int] = set()
        self._activity_hide_source: int | None = None
        self._last_finished_task_id: int | None = None
        self.activity_feedback = ActivityFeedback(limit=20)
        self.ui_perf = UiPerfMonitor()
        self.covers = CoverLoader(diagnostics=diagnostics, max_workers=6)
        self.history_cover_resolver = HistoryCoverResolver(service, diagnostics)
        self.runner = TaskRunner(self)
        _TRACE_LOG.info("TRACE gui init pre-css")
        self._install_css()
        _TRACE_LOG.info("TRACE gui init post-css")

        width = int(config.get("ui.width", 1320))
        height = int(config.get("ui.height", 840))
        self.set_default_size(width, height)
        self.set_size_request(760, 540)
        self.set_title(f"cifereca — v{__version__}")

        self.window_root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(self.window_root)
        self.root_stack = Gtk.Stack()
        self.root_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.root_stack.set_vexpand(True)
        self.window_root.append(self.root_stack)

        notification = build_notification_bar(Gtk=Gtk, add_css=add_css)
        self.notification_revealer = notification.revealer
        self.notification_label = notification.label
        self.notification_close = notification.close
        self.notification_close.connect("clicked", lambda _button: self._dismiss_notification())
        self.window_root.append(self.notification_revealer)
        self._notification_hide_source: int | None = None
        startup_started = time.perf_counter()
        _TRACE_LOG.info("TRACE gui build-startup begin")
        self.root_stack.add_named(self._build_startup(), "startup")
        _TRACE_LOG.info("TRACE gui build-startup end")
        _TRACE_LOG.info("TRACE gui build-main-shell begin")
        self.main_shell = self._build_main_shell()
        self.root_stack.add_named(self.main_shell, "main")
        # Identities are Core configuration, not plugin state. Populate them
        # immediately so a deferred/plugin UI failure cannot blank the selector.
        self._populate_accounts()
        self.root_stack.set_visible_child_name("main")
        shell_ms = (time.perf_counter() - startup_started) * 1000.0
        self.ui_perf.record("startup.shell_interactive", shell_ms)
        _TRACE_LOG.info("TRACE gui shell-interactive duration_ms=%.1f", shell_ms)
        self.connect("close-request", self._on_close_request)
        self._install_shell_shortcuts()
        # Provider sessions are secondary startup work. They are scheduled
        # after deferred plugin activation so provider services are available.

    def action_button(
        self, label: str, icon_name: str | None = None, *,
        suggested: bool = False, destructive: bool = False, tooltip: str | None = None,
    ) -> Gtk.Button:
        """Create an action using the highest-priority ``ui.widgets`` catalog."""
        try:
            catalog = self.plugin_manager.widget_catalog() if self.plugin_manager is not None else None
        except Exception:
            catalog = None
        role = "action.primary" if suggested else ("action.destructive" if destructive else "action.secondary")
        if catalog is not None:
            try:
                spec = catalog.describe(role) or {}
            except Exception:
                spec = {}
            icon_name = str(spec.get("icon_name") or icon_name or "") or None
            suggested = bool(spec.get("suggested", suggested))
            destructive = bool(spec.get("destructive", destructive))
        return action_button(label, icon_name, suggested=suggested, destructive=destructive, tooltip=tooltip)

    # ---------- infrastructure ----------
    def _install_css(self) -> None:
        return shell_stylesheet_controller.install_css(
            self, Gtk=Gtk, Gdk=Gdk,
        )

    def _cancel_notification_hide(self) -> None:
        return feedback_timers.cancel_notification_hide(self, source_remove=GLib.source_remove)

    def _hide_notification(self) -> bool:
        return feedback_timers.hide_notification(self)

    def _dismiss_notification(self) -> None:
        if getattr(self, "_closed", False):
            return
        return feedback_timers.dismiss_notification(self)

    def toast(self, message: str, timeout: int = 3) -> None:
        if getattr(self, "_closed", False):
            return
        return feedback_timers.toast(self, message, timeout, timeout_add_seconds=GLib.timeout_add_seconds)

    def show_error(self, message: str, title: str = "Une erreur est survenue") -> None:
        if getattr(self, "_closed", False):
            return
        return feedback_dialog_controller.show_error(self, message, title, Adw=Adw)

    def show_attention(self, message: str, *, code: str = "attention_required") -> None:
        """Present a remediable condition without framing it as a crash."""
        if getattr(self, "_closed", False):
            return
        return feedback_dialog_controller.show_attention(
            self, message, code=code, Adw=Adw,
        )

    def _cancel_activity_hide(self) -> None:
        return feedback_timers.cancel_activity_hide(self, source_remove=GLib.source_remove)

    def _refresh_activity_history(self) -> None:
        return activity_feedback_view_controller.refresh_activity_history(
            self, Gtk=Gtk, clear_box=clear_box, activity_history_rows=activity_history_rows,
        )

    def _show_pending_activity_error(self) -> bool:
        return activity_feedback_view_controller.show_pending_activity_error(self)

    def _hide_activity_feedback(self) -> bool:
        return activity_feedback_view_controller.hide_activity_feedback(
            self, can_reduce_activity=can_reduce_activity,
        )

    def _dismiss_activity_feedback(self) -> None:
        return activity_feedback_view_controller.dismiss_activity_feedback(self)

    def _schedule_activity_reduce(self, delay: int = 4) -> None:
        if getattr(self, "_closed", False):
            return
        return feedback_timers.schedule_activity_reduce(self, delay, timeout_add_seconds=GLib.timeout_add_seconds)

    def _activity_result(
        self,
        message: str,
        *,
        success: bool = True,
        persistent: bool = False,
        task_id: int | None = None,
    ) -> None:
        return activity_result_controller.activity_result(
            self, message, success=success, persistent=persistent, task_id=task_id,
        )

    def _task_started(self, task_id: int, count: int, label: str, priority: int, kind: str) -> bool:
        if getattr(self, '_closed', False):
            return False
        return task_activity_adapter.task_started(self, task_id, count, label, priority, kind)

    def _task_progress(self, label: str) -> bool:
        if getattr(self, '_closed', False):
            return False
        return task_activity_adapter.task_progress(self, label)

    def _task_progress_for_task(self, task_id: int, label: str) -> bool:
        return task_activity_adapter.task_progress(self, label, task_id=task_id)

    def _task_finished(self, task_id: int, remaining: int, label: str, success: bool, error: str | None) -> bool:
        if getattr(self, '_closed', False):
            return False
        return task_activity_adapter.task_finished(self, task_id, remaining, label, success, error)

    @staticmethod
    def _functional_error_message(message: str) -> str:
        text = str(message or "").strip()
        lowered = text.lower()
        if "adobe drm as not authorized/default" in lowered or "not authorized/default" in lowered:
            return "Le profil ADE sélectionné n’est pas autorisé pour cette opération"
        return text or "L’opération a échoué"

    def _busy_changed(self, count: int, label: str) -> bool:
        # Compatibility shim for older progress callbacks. New work should use
        # _task_progress so it cannot accidentally hide another active task.
        if count:
            return self._task_progress(label)
        return False

    def _on_close_request(self, *_args) -> bool:
        self._dispose_maintenance_lines()
        return close_window_resources(self, source_remove=GLib.source_remove)

    # ---------- provider startup ----------
    def _build_startup(self) -> Gtk.Widget:
        return build_startup_page(self)

    def connect_configured_sources(self) -> bool:
        if getattr(self, "_closed", False):
            return False
        return startup_coordinator.connect_configured_sources(
            self, idle_add=GLib.idle_add,
            timeout_add_seconds=getattr(GLib, "timeout_add_seconds", None),
            source_remove=getattr(GLib, "source_remove", None),
        )

    def reconnect_sources(self) -> None:
        self.connect_configured_sources()

    def _connect_sources_if_open(self) -> bool:
        """One-shot GTK callback; never connect after window teardown."""
        return once_while_open(self, self.connect_configured_sources)()

    def activate_startup_plugins(self) -> bool:
        if getattr(self, "_closed", False):
            return False
        return startup_coordinator.activate_startup_plugins(self, idle_add=GLib.idle_add)

    # ---------- compatibility / source configuration ----------
    def logout(self) -> None:
        # Kept as a compatibility alias: credentials are persistent provider
        # preferences now, so there is no login screen to return to.
        self.reconnect_sources()

    def _open_prelogin_diagnostics(self) -> None:
        return standalone_settings_controller.open_standalone_settings(
            self, Gtk=Gtk, Adw=Adw, Gdk=Gdk,
        )

    # ---------- shell / navigation ----------
    def _build_main_shell(self) -> Gtk.Widget:
        return shell_body_controller.build_main_shell(
            self, Gtk=Gtk, Pango=Pango, add_css=add_css, version=__version__,
        )

    _UiHost = _UiHost

    def _plugins_changed(self) -> None:
        if getattr(self, "_closed", False):
            return
        return plugin_reload_controller.plugins_changed(self)

    def _subscribe_cached_book_details(self) -> None:
        if getattr(self, "_closed", False):
            return
        return plugin_reload_controller.subscribe_cached_book_details(
            self, idle_add=GLib.idle_add,
            subscribe_cached_book_detail_changes=subscribe_cached_book_detail_changes,
        )

    def _cached_book_cache_invalidated(self) -> bool:
        if self._closed:
            return False
        context = self._cached_book_context
        if context is not None:
            route, key, item = context
            self._request_cached_book_details(route, key, item)
        return False

    def _cancel_cached_book_details(self) -> None:
        self._cached_book_request_generation += 1
        self._cached_book_context = None

    def _request_cached_book_details(self, route: str, key: str, item: dict) -> None:
        """Schedule a bounded, cache-only plugin lookup outside GTK."""
        self._cached_book_request_generation += 1
        request = self._cached_book_request_generation
        self._cached_book_context = (route, key, item)
        detail = self.search_page.detail if route == "search" else self.library_page.detail
        detail.update_cached_book_details(())
        services = self._cached_book_services
        identity = self.service.current_identity
        if not services or identity is None:
            return
        identity_id = identity.identity_id
        identity_generation = self._identity_context_generation
        book = book_ref_from_row(item)
        if not book.book_id:
            return

        def current() -> bool:
            active = self.service.current_identity
            return (
                not self._closed
                and request == self._cached_book_request_generation
                and self._cached_book_context is not None
                and self._cached_book_context[0:2] == (route, key)
                and active is not None and active.identity_id == identity_id
                and identity_generation == self._identity_context_generation
            )

        def done(contributions) -> None:
            if current():
                detail.update_cached_book_details(contributions)

        self.runner.submit(
            "Lecture des compléments en cache…",
            lambda: collect_cached_book_details(services, book, identity_id=identity_id),
            done, lambda _exc: None,
            priority=TaskPriority.METADATA, kind="detail-cache",
        )

    def _add_nav_row(self, route: str, label: str, icon_name: str) -> None:
        return shell_layout_controller.add_nav_row(
            self, route, label, icon_name, Gtk=Gtk,
        )

    def _build_standard_page(self, route):
        return shell_page_controller.build_standard_page(
            self, route, Gtk=Gtk, PluginManagerPage=PluginManagerPage,
        )

    def _rebuild_plugin_pages(self) -> None:
        return shell_page_controller.rebuild_plugin_pages(
            self, Gtk=Gtk, Pango=Pango, clear_box=clear_box, resolve_pages=resolve_pages,
        )
    def _toggle_nav_group(self, group, expanded):
        self._nav_group_expanded[group] = expanded
        for route, row_group in self._nav_groups.items():
            if row_group == group:
                self.nav_rows[route].set_visible(expanded)

    def _install_shell_shortcuts(self):
        return shell_shortcut_controller.install_shell_shortcuts(self, Gio=Gio)

    def _quit_application(self):
        return shell_shortcut_controller.quit_application(self)

    def _open_pages_menu(self):
        return shell_shortcut_controller.open_pages_menu(self)

    def _focus_search(self):
        return shell_shortcut_controller.focus_search(self)

    def _show_settings(self):
        return shell_shortcut_controller.show_settings(self)

    def _cycle_page(self, direction):
        return shell_shortcut_controller.cycle_page(self, direction)

    def _show_shortcuts(self):
        return shell_shortcut_controller.show_shortcuts(self, Adw=Adw)

    def _create_shell_header(self):
        return shell_header_controller.create_shell_header(
            self, Gtk=Gtk, Adw=Adw, Gio=Gio, Pango=Pango,
        )

    def _ensure_pages_menu_rightmost(self):
        return shell_layout_controller.ensure_pages_menu_rightmost(self)

    def _refresh_header_actions(self, route):
        return header_action_controller.refresh_header_actions(
            self, route, Gtk=Gtk, Gio=Gio, clear_box=clear_box,
            validate_action_selection=validate_action_selection,
            visible_actions=visible_actions, menu_sections=menu_sections,
        )

    def _invoke_ui_action(self, action, host):
        return plugin_action_dispatch_controller.invoke_ui_action(self, action, host)

    def _show_route(self, route: str) -> None:
        return route_display_controller.show_route(self, route, Gtk=Gtk, idle_add=GLib.idle_add)

    def _navigation_state(self, route: str):
        return navigation_controller.navigation_state(self, route)

    def _update_navigation_buttons(self) -> None:
        return navigation_controller.update_navigation_buttons(self)

    def _visit_route(self, route: str) -> None:
        return navigation_controller.visit_route(self, route)

    def _restore_navigation_state(self, state) -> None:
        return navigation_controller.restore_navigation_state(self, state)

    def can_go_back(self) -> bool:
        return navigation_controller.can_go_back(self)

    def can_go_forward(self) -> bool:
        return navigation_controller.can_go_forward(self)

    def go_back(self) -> None:
        return navigation_controller.go_back(self)

    def go_forward(self) -> None:
        return navigation_controller.go_forward(self)

    def _nav_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        return navigation_controller.nav_selected(self, _listbox, row)

    def navigate(self, route: str) -> None:
        return navigation_controller.navigate(self, route)

    def _replace_dropdown_labels(self, dropdown, labels):
        return shell_identity_selector_controller.replace_dropdown_labels(
            dropdown, labels, Gtk=Gtk,
        )

    def _populate_account_dropdown(self, identity_id: str | None) -> None:
        return shell_identity_selector_controller.populate_account_dropdown(
            self, identity_id, Gtk=Gtk, delegated_accounts=delegated_accounts,
        )

    def _populate_accounts(self) -> None:
        return shell_identity_selector_controller.populate_accounts(self, Gtk=Gtk)

    def _identity_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        return shell_account_selection_controller.identity_changed(self, dropdown, _pspec)

    def open_identity_library(self, identity_id: str) -> None:
        self._activate_identity(identity_id)
        self.navigate("library")

    def open_identity_view(self, identity_id: str, *, route: str, providers=()) -> None:
        return identity_view_controller.open_identity_view(
            self, identity_id, route=route, providers=providers,
        )

    def _activate_identity(self, identity_id: str) -> None:
        return identity_activation_controller.activate_identity(self, identity_id)

    def _account_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        return shell_account_selection_controller.account_changed(self, dropdown, _pspec)

    def _build_dashboard_page(self) -> Gtk.Widget:
        return dashboard_page_controller.build_dashboard_page(
            self, Gtk=Gtk, add_css=add_css, AdaptiveActionBar=AdaptiveActionBar,
        )

    @staticmethod
    def _sync_status_label(status: str | None) -> str:
        return {"running": "En cours", "success": "Réussie", "partial": "Partielle", "failed": "Échouée"}.get(str(status or ""), "Jamais synchronisée")

    def _refresh_statistics(self):
        return statistics_presentation_controller.refresh_statistics(self)

    def _show_statistics(self):
        return statistics_presentation_controller.show_statistics(self, Adw=Adw)

    def _render_dashboard(self) -> None:
        return dashboard_render_controller.render_dashboard(
            self, Gtk=Gtk, add_css=add_css,
        )

    def _choose_identity_directory(self, entry, root: Path) -> None:
        return settings_chooser_controller.choose_identity_directory(
            self, entry, root, Gtk=Gtk, Gio=Gio,
        )

    def _choose_adept_profile(self, entry, control) -> None:
        return settings_chooser_controller.choose_adept_profile(
            self, entry, control, Adw=Adw,
        )

    def _show_sync_issues(self, card):
        return sync_issues_dialog_controller.show_sync_issues(self, card, Adw=Adw)

    def _build_sync_page(self):
        return sync_controller._build_sync_page(self)

    def _refresh_sync_page(self) -> None:
        return sync_controller._refresh_sync_page(self)

    def _build_tools_page(self):
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for edge in ("top", "bottom", "start", "end"):
            getattr(outer, "set_margin_" + edge)(20)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Outils", xalign=0); title.set_hexpand(True); add_css(title, "page-title")
        refresh = self.action_button("Actualiser", "view-refresh-symbolic")
        heading.append(title); heading.append(refresh); outer.append(heading)
        self._tools_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8); outer.append(self._tools_box)
        refresh.connect("clicked", lambda _b: self._refresh_tools_page())
        scroll.set_child(outer); return scroll

    def _dispose_maintenance_lines(self) -> None:
        # All Maintenance rows are widget-scoped. A refresh or identity switch
        # cancels their workers and revokes any pending result display.
        for feedback, state in getattr(self, "_maintenance_line_rows", ()):
            feedback.dispose()
            control = state["control"]
            if control is not None:
                control.cancel()
        self._maintenance_line_rows = []
        for view in getattr(self, "_maintenance_result_views", ()):
            view.clear()
        self._maintenance_result_views = []

    def _refresh_tools_page(self) -> None:
        box = getattr(self, "_tools_box", None)
        if box is None: return
        self._dispose_maintenance_lines()
        clear_box(box)

        services = self.plugin_manager.services("maintenance.tool") if self.plugin_manager else ()
        if not services:
            label = Gtk.Label(label="Aucun outil de maintenance additionnel n’est installé.", xalign=0)
            add_css(label, "dim-label"); box.append(label); return
        for resolved in services:
            tool = resolved.service
            try: info = tool.describe()
            except Exception:
                # Provider exceptions can contain account data, URLs or paths.
                # Never put raw plugin exception text in a GTK maintenance row.
                box.append(Gtk.Label(label="Outil de maintenance : description indisponible.", xalign=0))
                continue
            group = Adw.PreferencesGroup(); box.append(group)
            row = Adw.ActionRow(); row.set_title(info.title); row.set_subtitle(info.description or resolved.plugin_id)
            label = "Prévisualiser…" if info.supports_dry_run else "Exécuter…"
            run = self.action_button(label, info.icon_name)
            row.add_suffix(run)
            cancel = Gtk.Button(label="Annuler")
            cancel.set_visible(False)
            row.add_suffix(cancel)
            group.add(row)
            active_cancel = {"control": None, "ticket": None}
            line_feedback = None
            line_scope = {"active": False, "identity": None, "generation": None}
            activity_labels = {
                "core.cache.cleanup": "Purge",
                "covers.audit": "Vérification des couvertures",
                "languages.audit": "Vérification des langues",
                "library.integrity.audit": "Vérification d’intégrité",
                "library.naming.audit": "Audit des noms",
                "library.naming.repair": "Renormalisation des noms",
                "library.duplicates.cleanup": "Nettoyage des doublons",
            }
            # A future maintenance.tool can opt into plan_with_progress
            # without shipping a CoreUI-specific ID mapping. Always mount an
            # independent safe row; only Core-owned labels are shown and
            # plugin progress prose remains discarded by the relay.
            activity_label = activity_labels.get(getattr(info, "id", None), "Traitement")
            if activity_label is not None:
                def line_alive(button=run, scope=line_scope):
                    if self._closed or button.get_root() is None:
                        return False
                    if not scope["active"]:
                        return True
                    current = self.service.current_identity
                    return (
                        (current.identity_id if current else None) == scope["identity"]
                        and getattr(self, "_identity_context_generation", None) == scope["generation"]
                    )
                status_widget, line_feedback = build_maintenance_line(
                    Gtk=Gtk, idle_add=GLib.idle_add, alive=line_alive,
                    activity_label=activity_label,
                )
                row.add_suffix(status_widget)
                self._maintenance_line_rows.append((line_feedback, active_cancel))

            def result_alive(button=run, scope=line_scope):
                current = self.service.current_identity
                return (
                    not self._closed and button.get_root() is not None
                    and scope["active"]
                    and (current.identity_id if current else None) == scope["identity"]
                    and getattr(self, "_identity_context_generation", None) == scope["generation"]
                )

            result_view = MaintenanceResultView(
                Gtk=Gtk, Adw=Adw, Pango=Pango, parent=self,
                row=row, title=info.title, alive=result_alive,
            )
            self._maintenance_result_views.append(result_view)

            def request_cancel(_cancel_button, cancellation_state=active_cancel,
                               feedback=line_feedback):
                active = cancellation_state["control"]
                if active is not None:
                    active.cancel()
                    _cancel_button.set_sensitive(False)
                    if feedback is not None and cancellation_state["ticket"] is not None:
                        feedback.finish(cancellation_state["ticket"], "cancelled")

            cancel.connect("clicked", request_cancel)

            def prepare(
                _button, service=tool, meta=info,
                cancel_button=cancel, cancellation_state=active_cancel,
                feedback=line_feedback, progress_scope=line_scope,
                result_view=result_view,
            ):
                if self._closed or _button.get_root() is None:
                    return
                result_view.clear()
                identity = self.service.current_identity
                context = MaintenanceContext(scope=meta.scope, identity_id=identity.identity_id if identity else None)
                destructive = meta.danger_level == "destructive"
                # Each enabled line owns safe, literal error wording. Never
                # display provider exceptions, which may contain private paths.
                preview_error = {
                    "covers.audit": "Analyse des couvertures impossible.",
                    "languages.audit": "Analyse des langues impossible.",
                    "library.integrity.audit": "Préparation de l’intégrité impossible.",
                    "library.naming.audit": "Analyse des noms impossible.",
                    "library.naming.repair": "Analyse des renommages impossible.",
                    "library.duplicates.cleanup": "Analyse des doublons impossible.",
                }.get(getattr(meta, "id", None), "Analyse des caches impossible.")
                execution_error = {
                    "covers.audit": "Vérification des couvertures impossible.",
                    "languages.audit": "Vérification des langues impossible.",
                    "library.integrity.audit": "Audit d’intégrité impossible.",
                    "library.naming.audit": "Audit des noms impossible.",
                    "library.naming.repair": "Renormalisation des noms impossible.",
                    "library.duplicates.cleanup": "Nettoyage des doublons impossible.",
                }.get(getattr(meta, "id", None), "Opération de maintenance indisponible.")
                context_generation = getattr(self, "_identity_context_generation", None)
                progress_scope.update(
                    active=True, identity=context.identity_id, generation=context_generation,
                )
                ticket = feedback.begin() if feedback is not None else None
                if feedback is not None and not feedback.valid(ticket):
                    return
                cancellation_state["ticket"] = ticket

                def maintenance_worker(callback, failure_message, worker_cancel_control=None):
                    # TaskRunner records errors in global activity before the
                    # row callback. Sanitize at the worker boundary as well.
                    try:
                        result = callback()
                        if feedback is not None and worker_cancel_control is not None:
                            worker_cancel_control.check_cancelled()
                        return result
                    except MaintenanceBusyError:
                        if feedback is None:
                            raise
                        raise MaintenanceBusyError(BUSY_MESSAGE) from None
                    except MaintenancePlanChangedError:
                        if feedback is None:
                            raise
                        raise MaintenancePlanChangedError(CHANGED_MESSAGE) from None
                    except MaintenanceCancelledError:
                        raise
                    except Exception:
                        if feedback is None:
                            raise
                        raise RuntimeError(failure_message) from None

                # A producer can opt in to plan_with_progress(context, control).
                # The worker emits *only bounded counters* to this Core-owned
                # row. Provider prose is discarded: no book title, path,
                # account ID or URL can reach GTK via a progress message.
                def preview_progress(current, total, _private_message):
                    if feedback is None or ticket is None:
                        return
                    if not (type(current) is int and type(total) is int
                            and 0 <= current <= total <= 1_000_000):
                        current = total = None
                    feedback.report(ticket, current=current, total=total)

                # Planning is cancellable even for legacy plan(context)
                # producers (whose result must be discarded after cancel).
                previous_control = cancellation_state["control"]
                if previous_control is not None:
                    previous_control.cancel()
                planning_control = MaintenanceCancelControl(
                    owner_closed=lambda: self._closed,
                    on_progress=(preview_progress if feedback is not None else None),
                )
                cancellation_state["control"] = planning_control
                can_cancel_preview = getattr(meta, "cancellable", False) is True
                cancel_button.set_visible(can_cancel_preview)
                cancel_button.set_sensitive(can_cancel_preview)

                def finish_preview():
                    if cancellation_state["control"] is not planning_control:
                        return False
                    cancellation_state["control"] = None
                    if not self._closed:
                        cancel_button.set_visible(False)
                    return True

                def present_preview(plan):
                    # The worker can finish after cancellation, navigation,
                    # identity changes or shutdown. Only the current plan may
                    # create a GTK dialog and all widget access stays on GTK.
                    if not finish_preview() or planning_control.cancelled():
                        return
                    if self._closed or _button.get_root() is None:
                        return
                    if feedback is not None and (
                        not feedback.valid(ticket)
                        or getattr(self, "_identity_context_generation", None) != context_generation
                    ):
                        return
                    current_identity = self.service.current_identity
                    if (current_identity.identity_id if current_identity else None) != context.identity_id:
                        if feedback is not None:
                            feedback.finish(ticket, "cancelled")
                        self.show_error(
                            "L’identité active a changé. Relancez la prévisualisation.",
                            meta.title,
                        )
                        return
                    if feedback is not None:
                        feedback.confirmation(ticket)
                    scope_labels = {
                        "global": "Application entière",
                        "identity": "Identité active",
                        "library": "Bibliothèque",
                        "artifact": "Document ciblé",
                        "storage.target": "Cible de stockage",
                    }
                    scope = scope_labels.get(str(meta.scope), str(meta.scope or "Non précisée"))
                    if meta.scope == "identity" and identity is not None:
                        scope += f" · {identity.label}"
                    sections = [
                        "Résumé :\n" + (str(plan.summary or "").strip() or "Aucun résumé fourni."),
                        "Changements prévus :\n" + (
                            "\n".join("• " + str(value) for value in plan.changes[:20])
                            if plan.changes else "• Aucun changement prévu"
                        ),
                        "Avertissements :\n" + (
                            "\n".join("• " + str(value) for value in plan.warnings[:10])
                            if plan.warnings else "• Aucun"
                        ),
                        "Portée :\n" + scope,
                    ]
                    # Keep action responses in the dialog footer, outside the
                    # scrolling preview. Long paths must wrap rather than
                    # expanding the dialog beyond the available screen.
                    dialog = Adw.MessageDialog.new(self, meta.title, "")
                    preview_text = Gtk.Label(label="\n\n".join(sections))
                    preview_text.set_wrap(True)
                    preview_text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    preview_text.set_xalign(0)
                    preview_text.set_yalign(0)
                    preview_text.set_selectable(True)
                    preview_text.set_max_width_chars(76)
                    preview_text.set_focusable(True)
                    preview_scroll = Gtk.ScrolledWindow()
                    preview_scroll.set_policy(
                        Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC,
                    )
                    preview_scroll.set_min_content_height(100)
                    preview_scroll.set_max_content_height(340)
                    preview_scroll.set_propagate_natural_height(True)
                    preview_scroll.set_child(preview_text)
                    dialog.set_extra_child(preview_scroll)
                    dialog.add_response("cancel", "Annuler"); dialog.add_response("run", "Exécuter")
                    if destructive:
                        dialog.set_response_appearance("run", Adw.ResponseAppearance.DESTRUCTIVE)
                    else:
                        dialog.set_response_appearance("run", Adw.ResponseAppearance.SUGGESTED)

                    def response(_dialog, selected):
                        if cancellation_state["ticket"] != ticket:
                            return
                        if selected != "run" or self._closed:
                            if feedback is not None:
                                feedback.finish(ticket, "cancelled")
                            return
                        if feedback is not None and (
                            not feedback.valid(ticket) or feedback.state.phase != "confirmation"
                            or getattr(self, "_identity_context_generation", None) != context_generation
                            or _button.get_root() is None
                        ):
                            return
                        current_identity = self.service.current_identity
                        if (current_identity.identity_id if current_identity else None) != context.identity_id:
                            if feedback is not None:
                                feedback.finish(ticket, "cancelled")
                            self.show_error(
                                "L’identité active a changé. Relancez la prévisualisation.",
                                meta.title,
                            )
                            return
                        if feedback is not None:
                            feedback.running(ticket)

                        def report_progress(current, total, message):
                            if feedback is not None:
                                feedback.report(ticket, current=current, total=total, message=message)

                        worker_control = MaintenanceCancelControl(
                            # Core's broker already checks the private
                            # full scope cooperatively inside the worker. Do
                            # not rehash all configured roots for every EPUB
                            # chunk on that worker thread; GTK revalidates at
                            # refresh and terminal publication.
                            owner_closed=lambda: self._closed,
                            on_progress=report_progress if feedback is not None else None,
                        )
                        cancellation_state["control"] = worker_control
                        can_cancel = getattr(meta, "cancellable", False) is True
                        cancel_button.set_visible(can_cancel)
                        cancel_button.set_sensitive(can_cancel)

                        def finish_maintenance() -> bool:
                            if cancellation_state["control"] is not worker_control:
                                return False
                            cancellation_state["control"] = None
                            if self._closed or _button.get_root() is None:
                                return False
                            cancel_button.set_visible(False)
                            current_identity = self.service.current_identity
                            if (current_identity.identity_id if current_identity else None) != context.identity_id:
                                return False
                            if getattr(self, "_identity_context_generation", None) != context_generation:
                                return False
                            return feedback is None or feedback.valid(ticket)

                        def completed(result) -> None:
                            # Other Maintenance tools remain widget-scoped.
                            if feedback is not None and worker_control.cancelled():
                                failed(MaintenanceCancelledError("Opération de maintenance annulée"))
                                return
                            if not finish_maintenance():
                                return
                            if feedback is not None:
                                feedback.finish(ticket, "success" if result.ok else "error")
                            result_view.publish(result)
                            self._activity_result(
                                result.summary or ("Outil terminé" if result.ok else "Outil terminé avec erreurs"),
                                success=bool(result.ok),
                                persistent=not bool(result.ok),
                            )

                        def failed(exc) -> None:
                            if not finish_maintenance():
                                return
                            if feedback is not None:
                                feedback.finish(ticket, "cancelled" if isinstance(exc, MaintenanceCancelledError) else "error")
                            if isinstance(exc, MaintenanceCancelledError):
                                self.show_error(
                                    "Traitement interrompu ; les modifications déjà effectuées ne sont pas annulées.",
                                    meta.title,
                                )
                            else:
                                self.show_error(
                                    BUSY_MESSAGE if feedback is not None and isinstance(exc, MaintenanceBusyError)
                                    else CHANGED_MESSAGE if feedback is not None and isinstance(exc, MaintenancePlanChangedError)
                                    else (execution_error if feedback is not None else str(exc)),
                                    meta.title,
                                )

                        # Plan revalidation and mutations hold the Core lock.
                        # The separate cancellation button sets a thread-safe
                        # event; each plugin check_cancelled() observes it.
                        self.runner.submit(
                            meta.title,
                            lambda: maintenance_worker(
                                lambda: execute_maintenance_tool(
                                    service, context, plan, worker_control, destructive=destructive,
                                ),
                                execution_error, worker_control,
                            ),
                            completed,
                            failed,
                            control=_button,
                            priority=TaskPriority.MAINTENANCE,
                            kind="maintenance-tool",
                        )
                    dialog.connect("response", response); dialog.present()

                def preview_failed(exc):
                    if not finish_preview() or planning_control.cancelled():
                        if feedback is not None and planning_control.cancelled():
                            feedback.finish(ticket, "cancelled")
                        return
                    if self._closed or _button.get_root() is None:
                        return
                    if feedback is not None:
                        feedback.finish(ticket, "error")
                    self.show_error(
                        BUSY_MESSAGE if feedback is not None and isinstance(exc, MaintenanceBusyError)
                        else CHANGED_MESSAGE if feedback is not None and isinstance(exc, MaintenancePlanChangedError)
                        else (preview_error if feedback is not None else str(exc)),
                        meta.title,
                    )

                # Planning can scan thousands of placements. Never call
                # service.plan(context) on the main GTK event loop.
                self.runner.submit(
                    f"Analyse : {meta.title}",
                    lambda: maintenance_worker(
                        lambda: preview_maintenance_tool(
                            service, context, destructive=destructive, control=planning_control,
                        ),
                        preview_error, planning_control,
                    ),
                    present_preview,
                    preview_failed,
                    control=_button,
                    priority=TaskPriority.MAINTENANCE,
                    kind="maintenance-plan",
                )
            run.connect("clicked", prepare)

    def run_opening_sync(self, control: Gtk.Widget | None = None, *, deep: bool = False) -> None:
        return sync_controller.run_opening_sync(self, control, deep=deep)

    def _known_item_languages(self, provider_id: str, item_id: str | None, metadata: dict[str, Any] | None = None) -> set[str]:
        return known_languages_controller.known_item_languages(
            self, provider_id, item_id, metadata,
        )

    def _build_loans_page(self) -> MasterDetailPage:
        return loans_controller._build_loans_page(self)

    def _loan_filter_toggled(self, button: Gtk.ToggleButton) -> None:
        return loans_controller._loan_filter_toggled(self, button)

    def _populate_loans(self, rows: list[Loan]) -> None:
        return loans_controller._populate_loans(self, rows)

    def _render_loans(self) -> None:
        return loans_controller._render_loans(self)

    def _loan_selected(self, row: BookListItem | None) -> None:
        return loans_controller._loan_selected(self, row)

    def refresh_loans(self, force: bool = True, control: Gtk.Widget | None = None) -> None:
        return loans_controller.refresh_loans(self, force, control)

    def _loans_refreshed(self, rows):
        return loans_controller._loans_refreshed(self, rows)

    def confirm_renew(self, loan: Loan, control: Gtk.Widget) -> None:
        return loans_controller.confirm_renew(self, loan, control)

    def _renew(self, loan: Loan, control: Gtk.Widget) -> None:
        return loans_controller._renew(self, loan, control)

    def confirm_return(self, loan: Loan, control: Gtk.Widget) -> None:
        return loans_controller.confirm_return(self, loan, control)

    def _return_loan_result(self, result: Any, *, channel: str) -> None:
        return loans_controller._return_loan_result(self, result, channel=channel)

    def _return_loan(self, loan: Loan, control: Gtk.Widget) -> None:
        return loans_controller._return_loan(self, loan, control)

    def _return_loan_pnb(self, loan: Loan, control: Gtk.Widget) -> None:
        return loans_controller._return_loan_pnb(self, loan, control)

    # ---------- search ----------
    def _query_controls(self, changed, activate, *, library=False):
        return query_widget_factory_controller.build_query_controls(
            self, changed, activate, library=library, QueryControls=QueryControls,
        )

    def _reset_search_enter(self):
        return search_controller._reset_search_enter(self)

    def _search_controls_changed(self):
        return search_controller._search_controls_changed(self)

    def _search_enter(self):
        return search_controller._search_enter(self)

    def _search_key_interaction(self, _controller, keyval, _keycode, _state):
        return search_controller._search_key_interaction(self, _controller, keyval, _keycode, _state)

    def _build_search_page(self) -> MasterDetailPage:
        return search_controller._build_search_page(self)

    def search_books(
        self, query: str, *, field: str = "title",
        online: bool = False, identity_id: str | None = None,
        providers: tuple[str, ...] = (),
    ) -> bool:
        """Search from a trusted book detail; exactly one explicit query.

        This API is public through UiSearchNavigationHost. No credentials,
        selected identity or old filters are copied into a new search.
        """
        return search_controller.search_books(self, query, field=field, online=online, identity_id=identity_id, providers=providers)

    def can_open_loaded_search_notice(
        self, *, provider: str, credential: str, record_id: str,
        docbase: str, source_collection: str, identity_id: str,
    ) -> bool:
        from .exact_search_notice_navigation import can_open_loaded_search_notice
        return can_open_loaded_search_notice(
            self, provider=provider, credential=credential,
            record_id=record_id, docbase=docbase,
            source_collection=source_collection, identity_id=identity_id,
        )

    def open_loaded_search_notice(
        self, *, provider: str, credential: str, record_id: str,
        docbase: str, source_collection: str, identity_id: str,
    ) -> bool:
        from .exact_search_notice_navigation import open_loaded_search_notice
        return open_loaded_search_notice(
            self, provider=provider, credential=credential,
            record_id=record_id, docbase=docbase,
            source_collection=source_collection, identity_id=identity_id,
        )

    def can_open_known_search_notice(
        self, *, provider: str, credential: str, record_id: str,
        docbase: str, source_collection: str, identity_id: str,
    ) -> bool:
        from .known_search_notice_navigation import can_open_known_search_notice
        return can_open_known_search_notice(
            self, provider=provider, credential=credential,
            record_id=record_id, docbase=docbase,
            source_collection=source_collection, identity_id=identity_id,
        )

    def open_known_search_notice(
        self, *, provider: str, credential: str, record_id: str,
        docbase: str, source_collection: str, identity_id: str,
    ) -> bool:
        from .known_search_notice_navigation import open_known_search_notice
        return open_known_search_notice(
            self, provider=provider, credential=credential,
            record_id=record_id, docbase=docbase,
            source_collection=source_collection, identity_id=identity_id,
        )

    def run_search(self, force: bool = False, *, keyboard: bool = False) -> None:
        from .search_host_port import SearchRunPort
        return search_controller.run_search(SearchRunPort(self), force, keyboard=keyboard)

    def _sync_search_detail_after_results(self) -> None:
        return search_results._sync_search_detail_after_results(self)

    def _populate_local_search(self, rows: list[dict[str, Any]]) -> None:
        return search_results._populate_local_search(self, rows)

    def _populate_search(self, rows: list[SearchResult], *, announce: bool = True) -> None:
        return search_results._populate_search(self, rows, announce=announce)

    def _render_search_results(self) -> int:
        return search_results._render_search_results(self)

    def _update_search_count(self, shown: int) -> None:
        return search_results._update_search_count(self, shown)


    def _search_selected(self, row: BookListItem | None) -> None:
        from .search_host_port import SearchDetailPort
        return search_details._search_selected(SearchDetailPort(self), row)

    def _search_detail_request_context(self) -> tuple[int, int, int, str | None]:
        from .search_host_port import SearchDetailPort
        return search_details._search_detail_request_context(SearchDetailPort(self))

    def _search_detail_context_current(
        self, selected_id: str, context: tuple[int, int, int, str | None] | None,
    ) -> bool:
        from .search_host_port import SearchDetailPort
        return search_details._search_detail_context_current(SearchDetailPort(self), selected_id, context)

    def _apply_search_detail(
        self, selected_id: str, item: SearchResult, detail: ItemDetail,
        *, context: tuple[int, int, int, str | None] | None = None,
    ) -> None:
        from .search_host_port import SearchDetailPort
        return search_details._apply_search_detail(SearchDetailPort(self), selected_id, item, detail, context=context)

    def _search_detail_error(
        self, selected_id: str, item: SearchResult, exc: BaseException,
        *, context: tuple[int, int, int, str | None] | None = None,
    ) -> None:
        from .search_host_port import SearchDetailPort
        return search_details._search_detail_error(SearchDetailPort(self), selected_id, item, exc, context=context)

    # ---------- history ----------
    def _build_history_page(self) -> MasterDetailPage:
        return secondary_page_controller.build_history_page(
            self, Gtk=Gtk, MasterDetailPage=MasterDetailPage, MultiSelect=MultiSelect,
        )

    def _identity_request_current(self, identity_id: str | None, generation: int) -> bool:
        current = self.service.current_identity
        current_identity_id = current.identity_id if current is not None else None
        return (
            generation == self._identity_context_generation
            and identity_id == current_identity_id
        )

    def refresh_history(self, force: bool = False, control: Gtk.Widget | None = None) -> None:
        current = self.service.current_identity
        identity_id = current.identity_id if current is not None else None
        generation = self._identity_context_generation
        account = self.service.current_account
        account_generation = self._account_context_generation

        def done(rows: list[HistoryItem]) -> None:
            if (self._identity_request_current(identity_id, generation)
                    and account_generation == self._account_context_generation
                    and self.service.current_account is account):
                self._populate_history(rows)

        self.runner.submit(
            "Chargement de l'historique…", lambda: self.service.refresh_history(force=force), done, control=control,
            priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.NORMAL, kind="history",
        )

    def _history_book_row(self, x: HistoryItem) -> BookListItem:
        row = BookListItem(
            x.id, x.title, x.author or "",
            f"Emprunté le {fr_date(x.borrowed_at)} · {x.year or x.document_type or ''}",
            "Numérique" if x.medium == "digital" else "Physique",
            x.thumbnail_url,
            # 0.2.7: ne plus lancer de recherches catalogue automatiques pour
            # reconstruire les vignettes de l'historique d'un compte lié. Le site
            # officiel présente la même absence de vignettes ; on conserve donc le
            # placeholder en attendant une étude des identifiants en amont.
            cover_provider=(x.provider if (not x.thumbnail_url and self.service.current_account.is_primary) else None),
            cover_record_id=(x.record_id if (not x.thumbnail_url and self.service.current_account.is_primary) else None),
            cover_title=x.title,
            cover_author=x.author,
            cover_year=x.year,
            default_thumbnail_url=x.default_thumbnail_url,
        )
        self._attach_row_facets([row],{x.id:{'author':x.author,'source_provider':x.source_provider}},self.search_controls)
        return row

    def _rerender_history(self) -> None:
        rows = list(getattr(self, '_history_all_rows', ()))
        self._populate_history(rows, preserve_all=True)

    def _populate_history(self, rows: list[HistoryItem], preserve_all: bool = False) -> None:
        return secondary_results_controller.populate_history(self, rows, preserve_all)
    def _history_scroll_changed(self, adjustment: Gtk.Adjustment) -> None:
        return secondary_results_controller.history_scroll_changed(
            self, adjustment, GLib=GLib,
        )
    def _append_history_chunk(self) -> bool:
        return secondary_results_controller.append_history_chunk(self)
    def _history_selected(self, row: BookListItem | None) -> None:
        if row:
            item = self.history_objects.get(row.key)
            if item:
                from dataclasses import asdict
                self.history_page.detail.show_history(item)
                self._add_facets(self.history_page.detail, asdict(item), self.search_controls)
                self._add_local_book_actions(self.history_page.detail, asdict(item))

    # ---------- bookings ----------
    def _build_bookings_page(self) -> MasterDetailPage:
        return secondary_page_controller.build_bookings_page(
            self, Gtk=Gtk, MasterDetailPage=MasterDetailPage,
        )

    def refresh_bookings(self, force: bool = False, control: Gtk.Widget | None = None) -> None:
        current = self.service.current_identity
        identity_id = current.identity_id if current is not None else None
        generation = self._identity_context_generation
        account = self.service.current_account
        account_generation = self._account_context_generation

        def done(rows: list[Booking]) -> None:
            if (self._identity_request_current(identity_id, generation)
                    and account_generation == self._account_context_generation
                    and self.service.current_account is account):
                self._populate_bookings(rows)

        self.runner.submit(
            "Chargement des réservations…", lambda: self.service.refresh_bookings(force=force), done, control=control,
            priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.NORMAL, kind="bookings",
        )

    def _populate_bookings(self, rows: list[Booking]) -> None:
        return secondary_results_controller.populate_bookings(
            self, rows, BookListItem=BookListItem,
            booking_presentation=booking_presentation, fr_date=fr_date,
        )
    def _booking_selected(self, row: BookListItem | None) -> None:
        if row:
            item = self.booking_objects.get(row.key)
            if item:
                from dataclasses import asdict
                self.booking_page.detail.show_booking(item)
                self._add_facets(self.booking_page.detail, asdict(item), self.search_controls)
                self._add_local_book_actions(self.booking_page.detail, asdict(item))

    # ---------- Bibliothèque fédérée ----------
    def _build_library_page(self) -> MasterDetailPage:
        return library_page_controller.build_library_page(
            self, Gtk=Gtk, MasterDetailPage=MasterDetailPage,
        )

    @staticmethod
    def _kobo_backup_status_label(status: str) -> str:
        return {
            "ok": "À jour",
            "complete": "À jour",
            "pending": "À acquérir",
            "pending_acquisition": "À acquérir",
            "acquiring": "Acquisition en cours",
            "reconcile_required": "À réconcilier",
            "reconcile_conflict": "Conflit local",
            "attention_required": "Action requise",
            "queued": "Ancien job 0.4.x",
            "claimed": "Ancien job 0.4.x",
            "downloading": "Ancien job 0.4.x",
            "processing": "Ancien job 0.4.x",
            "failed": "Erreur",
            "error": "Erreur",
            "cancelled": "Annulé",
        }.get(str(status or "").lower(), str(status or "Inconnu"))

    @staticmethod
    def _library_state_label(item: dict[str, Any]) -> str:
        state = str(item.get("state") or "")
        if state == "local":
            return "Local"
        if state == "remote_owned":
            # Core library_entries publishes this state only from an active
            # owned entitlement, never from a catalogue or a local placement.
            provider = item.get("provider_id")
            if provider == "kobo":
                return "Achat Kobo · en ligne"
            if provider == "numilog":
                return "Achat Numilog · en ligne"
            return "Droit de possession · en ligne"
        if state == "remote_archived":
            return "Kobo · archivé · en ligne"
        if state == "remote_loan_active":
            return "Emprunté"
        if state == "remote_loan_realized":
            return "Emprunt réalisé"
        if state == "available_to_borrow":
            return "Empruntable"
        if state == "available_to_buy":
            return "Achetable"
        if state == "remote_catalog":
            return "Notice de catalogue · sans droit attesté"
        if state == "missing":
            return "Fichier manquant"
        return state.replace("_", " ").strip().capitalize() or "Disponible"

    @classmethod
    def _library_availability_label(cls, item: dict[str, Any]) -> str:
        """Avoid repeating the local provenance as an identical availability."""
        provider = str(item.get("provider_id") or "local")
        state = str(item.get("state") or "")
        if provider == "local" and state == "local":
            return ""
        return cls._library_state_label(item)

    def _library_filter_toggled(self, button: Gtk.ToggleButton) -> None:
        if button.get_active():
            self.library_filter = button.get_name() or "all"
            self._render_library()

    def _library_search_online(self, _button: Gtk.Button) -> None:
        self.search_controls.set_state(self.library_controls.state())
        self.navigate("search")
        self.run_search(False)

    def choose_acsm(self):
        return acsm_import_chooser_controller.choose_acsm(self, Gtk=Gtk)

    def _search_facet(self, field, text, controls):
        return facet_value_controller.search_facet(self, field, text, controls)

    def _facet_values(self, item):
        from .book_facets import facets_for_book
        return [(f.field,f.label,f.value) for f in facets_for_book(item,self.service.provider_display_name)]


    def _facet_value_button(self, label, field, value, controls):
        return facet_value_controller.facet_value_button(
            self, label, field, value, controls, Gtk=Gtk, Pango=Pango,
        )

    def _facet_value_group(self, choices, field, controls):
        return facet_value_controller.facet_value_group(
            self, choices, field, controls, Gtk=Gtk,
        )

    def _attach_row_facets(self, rows, objects, controls):
        from .book_facets import facets_for_book, project_list_facets
        return list_facet_controller.attach_row_facets(
            self, rows, objects, controls,
            facets_for_book=facets_for_book,
            project_list_facets=project_list_facets,
        )

    def _add_facets(self, detail, item, controls):
        return detail_facets_controller.add_facets(
            self, detail, item, controls, Adw=Adw, Gtk=Gtk,
            TaskPriority=TaskPriority,
        )

    def _decorate_book_detail(self, detail, item):
        library_detail = getattr(getattr(self, 'library_page', None), 'detail', None)
        controls = self.library_controls if detail is library_detail else self.search_controls
        self._add_facets(detail, item, controls)
        self._add_local_book_actions(detail, item)
        self._add_plugin_book_actions(detail, item)

    def _book_action_context(self, item) -> UiActionContext:
        return book_action_context_controller.book_action_context(
            self, item, Path=Path, ArtifactRef=ArtifactRef,
            ArtifactFormat=ArtifactFormat, UiActionContext=UiActionContext,
            book_ref_from_row=book_ref_from_row,
        )

    def _add_plugin_book_actions(self, detail, item) -> None:
        return plugin_book_action_controller.add_plugin_book_actions(
            self, detail, item, contextual_actions=contextual_actions,
        )

    def _known_search_result(self, row):
        from ..core.known_catalog_provenance import known_catalog_search_result
        identity = getattr(self.service.current_identity, "identity_id", None)
        return known_catalog_search_result(row, identity_id=identity)

    def _load_known_detail(self, item):
        from .search_host_port import SearchDetailPort
        return search_details._load_known_detail(SearchDetailPort(self), item)

    def refresh_library(self, scan: bool = False, control: Gtk.Widget | None = None, *, refresh_covers: bool = False) -> None:
        return library_refresh.refresh_library(self, scan, control, refresh_covers=refresh_covers)

    def _show_library_entries(self, entries: list[dict[str, Any]]) -> None:
        self._library_entries_cache = list(entries)
        self._render_library(entries)
        if hasattr(self,'_refresh_statistics'):self._refresh_statistics()

    def open_book_detail(self, book: BookRef, *, identity_id: str | None = None) -> bool:
        """Open an attested book in the active identity's standard Library detail.

        Public plugins never navigate to another identity implicitly. Resolution
        is local-only; the Core rechecks identity context before dispatch.
        """
        return library_navigation.open_book_detail(
            self, book, identity_id=identity_id,
        )

    def _open_library_item(self, key: str) -> None:
        return library_navigation.open_library_item(self, key)
    def _select_pending_library_item(self) -> bool:
        return library_navigation.select_pending_library_item(self)
    def _render_library(self, entries: list[dict[str, Any]] | None = None) -> None:
        return library_render_controller.render_library(
            self, entries, perf_counter=time.perf_counter,
            project_library_rows=project_library_rows,
            BookListItem=BookListItem, fr_date=fr_date,
        )

    # ---------- Récupérations récentes ----------
    def _build_recent_page(self) -> MasterDetailPage:
        return library_page_controller.build_recent_page(
            self, Gtk=Gtk, MasterDetailPage=MasterDetailPage,
        )

    def refresh_recent(self, control: Gtk.Widget | None = None) -> None:
        return library_refresh.refresh_recent(self, control)

    def _show_recent_entries(self, items: list[dict[str, Any]]) -> None:
        self._recent_entries_cache = list(items)
        self._render_recent(items)

    def _render_recent(self, items: list[dict[str, Any]] | None = None) -> None:
        return library_render_controller.render_recent(
            self, items, perf_counter=time.perf_counter,
            project_recent_rows=project_recent_rows, BookListItem=BookListItem,
        )

    def _add_local_book_actions(self, detail, item):
        return local_book_action_controller.add_local_book_actions(
            self, detail, item,
            Gtk=Gtk, Gio=Gio, ArtifactRouteRequest=ArtifactRouteRequest,
            TaskPriority=TaskPriority,
            identity_has_attested_possession=identity_has_attested_possession,
        )

    def _open_placement(self, placement):
        try:
            current = self.service.current_identity
            if not current or current.identity_id != placement['identity_id']:
                raise ValueError('L’identité active a changé')
            uri = self.service.local_book_path(placement['placement_id'], identity_id=current.identity_id).as_uri()
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception as exc: self.show_error(str(exc), 'Ouverture du livre impossible')

    def _add_catalogue_action(self, detail, item):
        return public_catalogue_action_controller.add_catalogue_action(
            self, detail, item, Gio=Gio,
        )

    @staticmethod
    def _book_description(item):
        metadata = item.get('metadata') or {}
        return (metadata.get('epub_metadata') or {}).get('description') or metadata.get('description')

    def _add_book_notice(self, detail, item, *, include_description=True):
        return book_notice_controller.add_book_notice(
            self, detail, item, notice_rows=notice_rows,
            include_description=include_description,
        )

    def _recent_selected(self, row: BookListItem | None) -> None:
        return recent_detail_controller.recent_selected(
            self, row, Path=Path, format_recent_timestamp=format_recent_timestamp,
        )

    # Compatibility name used by the 0.8.0 refresh paths.
    def _render_kobo_loans(self) -> None:
        self._render_library()


    def _book_recommendation_target(self, item):
        return recommendation_target_controller.book_recommendation_target(
            self, item,
            BookReferenceRecommendationService=BookReferenceRecommendationService,
        )

    def _add_book_recommendation_action(self, detail, item) -> None:
        return book_recommendation_controller.add_book_recommendation_action(
            self, detail, item,
        )

    def _library_selected(self, row: BookListItem | None) -> None:
        return library_detail_controller.library_selected(
            self, row, Gtk=Gtk, unique_library_loan=unique_library_loan,
            book_detail_presentation=book_detail_presentation,
            detail_language=detail_language, document_links=document_links,
        )

    def _kobo_selected(self, row: BookListItem | None) -> None:
        self._library_selected(row)

    def _configure_kobo_backup_timer(self, *, run_now: bool = False) -> None:
        return sync_controller._configure_kobo_backup_timer(self, run_now=run_now)

    def run_kobo_backup(self, manual: bool = True, control: Gtk.Widget | None = None) -> None:
        return sync_controller.run_kobo_backup(self, manual, control)

    def choose_adept_loan(self, acquisition_id, loan, control):
        self.runner.submit("Lecture des prêts ADEPT…", lambda: self.service.adept_loan_choices(acquisition_id),
            lambda result: self._present_adept_loan_choices(acquisition_id, loan, result),
            lambda exc: self.show_error(str(exc), "Lecture des prêts impossible"), control=control,
            priority=TaskPriority.USER_ACTION, kind="adept-inventory")

    def _present_adept_loan_choices(self, acquisition_id, loan, result):
        return adept_loan_choice_controller.present_adept_loan_choices(
            self, acquisition_id, loan, result, Gtk=Gtk, Adw=Adw,
        )

    def reconcile_adept_from_detail(self, acquisition_id, loan, control):
        def ok(result):
            self.loan_page.detail.show_loan(loan)
            labels = {"resolved": "Liaison ADEPT vérifiée", "ambiguous": "Plusieurs prêts correspondent : liaison non résolue", "unresolved": "Aucun prêt ne correspond au chemin du fichier", "inventory_error": "Inventaire partiel : liaison non résolue", "profile_missing": "Associez un profil ADEPT à cette identité", "artifact_missing": "Fichier local absent", "binding_conflict": "L’inventaire contredit la liaison enregistrée"}
            self._activity_result(labels.get(result["status"], "Liaison ADEPT non résolue"))
        self.runner.submit("Vérification du prêt ADEPT…", lambda: self.service.reconcile_adept_acquisition(acquisition_id), ok,
            lambda exc: self.show_error(str(exc), "Vérification ADEPT impossible"), control=control,
            priority=TaskPriority.USER_ACTION, kind="adept-inventory")

    def return_adept_from_detail(self, acquisition_id: str, control: Gtk.Widget) -> None:
        def ok(_result):
            verification = _result.get("verification")
            suffix = " · état local vérifié" if verification == "confirmed" else (" · vérification locale non confirmée" if verification else "")
            message="Retour enregistré, jeton absent" if verification=="confirmed" else "Commande exécutée, retour non confirmé"
            result_message = message+" · fichiers conservés" + suffix
            reporter = getattr(self, "_activity_result", None)
            if callable(reporter):
                reporter(result_message)
            else:
                self.toast(result_message, 7)
            if getattr(self, "_displayed_loan", None):
                self.loan_page.detail.show_loan(self._displayed_loan)
            self.refresh_library(False)
            refresh = getattr(self, 'refresh_loans', None)
            if callable(refresh): refresh(True)
        self.runner.submit("Restitution du prêt ADEPT…", lambda: self.service.return_adept_acquisition(acquisition_id), ok,
            lambda exc: self.show_error(str(exc), "Restitution ADEPT impossible"), control=control,
            priority=TaskPriority.USER_ACTION, kind="adept-return")

    def prepare_bnf_acsm_for_loan(self, loan: Loan, control: Gtk.Widget | None = None) -> None:
        from .bnf_acsm_action_controller import prepare_bnf_acsm
        prepare_bnf_acsm(self, loan, control, Adw=Adw, TaskPriority=TaskPriority)

    def prepare_pnb_for_loan(self, loan: Loan, send: bool = False, control: Gtk.Widget | None = None) -> None:
        def work():
            if send:
                raise RuntimeError("L’envoi vers une liseuse nécessite un plugin storage.target.")
            return self.service.reacquire_digital_loan(loan)
        def ok(result):
            self.refresh_library(False); self.refresh_recent(); self._render_dashboard()
            self.loan_page.detail.show_loan(loan)
            # A completed TaskRunner callback is not evidence of a completed
            # book acquisition: Core may return attention_required or
            # retryable_error without throwing. Do not announce "Livre récupéré"
            # or mark the activity successful for those business outcomes.
            if type(result) is not dict or result.get("status") != "complete":
                from cifereca.core.diagnostic_loan_download import CODES
                state = result.get("status") if type(result) is dict else None
                detail = {
                    "attention_required": "Intervention requise pour récupérer le prêt",
                    "retryable_error": "Récupération du prêt à réessayer",
                }.get(state, "Récupération du prêt non confirmée")
                code = result.get("code") if type(result) is dict else None
                if type(code) is str and code in CODES:
                    detail += f" · code : {code}"
                reporter = getattr(self, "_activity_result", None)
                if callable(reporter):
                    reporter(detail, success=False)
                else:
                    self.show_error(detail, "Téléchargement non terminé")
                return
            suffix = " · restitution ADEPT disponible" if result.get("adept_return_available") else " · liaison ADEPT indisponible : voir le détail"
            result_message = "Livre récupéré" + suffix
            reporter = getattr(self, "_activity_result", None)
            if callable(reporter):
                reporter(result_message)
            else:
                self.toast(result_message, 6)
        self.runner.submit("Acquisition du livre numérique…", work, ok,
            lambda exc: self.show_error(str(exc), "Acquisition impossible"), control=control,
            priority=TaskPriority.USER_ACTION, kind="loan-acquisition")

    def can_borrow_item(self, item: SearchResult) -> bool:
        try:
            # The button must reflect the same exact borrower-account scope
            # enforced by the click handler, not just a provider capability.
            # This remains only action availability; quota, subscription and
            # per-book permission still require separate provider evidence.
            _session, accounts = self._eligible_borrow_accounts(item)
            return bool(accounts)
        except Exception:
            return False

    def _eligible_borrow_accounts(self, item: SearchResult):
        session = self.service._session_for_source(item.source_provider, item.source_credential)
        identity_id = getattr(self.service.current_identity, "identity_id", None)
        # Source capability is not borrower eligibility. Never offer a primary
        # account of another identity when the current identity has none.
        if (not session.capabilities.borrow or not session.connected
                or not identity_id
                or session.provider_id != item.source_provider
                or session.credential_id != item.source_credential):
            return session, []
        accounts = [
            account for account in session.accounts
            if (account.is_primary and account.identity_id == identity_id
                and account.provider_id == item.source_provider
                and account.credential_id == item.source_credential)
        ]
        # Multiple registrations for one borrower are ambiguous, not a
        # reason to silently pick the first account for a mutating action.
        if len({account.unique_id for account in accounts}) != len(accounts):
            return session, []
        return session, accounts

    def borrow_search_item(self, item: SearchResult, control: Gtk.Widget | None = None, *, account=None) -> None:
        try:
            session, accounts = self._eligible_borrow_accounts(item)
        except Exception as exc:
            self.show_error(str(exc), "Emprunt impossible"); return
        if not session.capabilities.borrow:
            self.show_error(f"{session.descriptor.display_name} : emprunt non pris en charge", "Emprunt impossible"); return
        if not accounts:
            self.show_error(f"{session.descriptor.display_name} : aucun compte principal disponible", "Emprunt impossible"); return
        if account is None and len(accounts) > 1:
            dialog = Adw.MessageDialog.new(self, "Choisir le compte d’emprunt", "Plusieurs comptes sont éligibles. Le compte utilisé doit être choisi explicitement.")
            dialog.add_response("cancel", "Annuler")
            for index, candidate in enumerate(accounts):
                response = f"account-{index}"
                label = candidate.display_name or candidate.barcode or candidate.unique_id or f"Compte {index + 1}"
                dialog.add_response(response, label)
            def selected(_dialog, response):
                if response.startswith("account-"):
                    index = int(response.split("-", 1)[1])
                    self.borrow_search_item(item, control, account=accounts[index])
            dialog.connect("response", selected); dialog.present(); return
        # A dialog may have been opened under identity A and clicked after
        # switching to B (or after disconnect/reconnect). Re-check the exact
        # registered account instead of trusting the captured callback.
        if account is not None and not any(candidate is account for candidate in accounts):
            self.show_error("Le compte sélectionné n'est plus disponible pour cette identité.", "Emprunt impossible")
            return
        account = account or accounts[0]
        identity_id = account.identity_id
        generation = self._identity_context_generation
        account_scope = (
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )

        def current() -> bool:
            if (getattr(self, "_closed", False)
                    or self._identity_context_generation != generation
                    or getattr(self.service.current_identity, "identity_id", None) != identity_id
                    or not session.connected
                    or (account.identity_id, account.provider_id,
                        account.credential_id, account.unique_id) != account_scope):
                return False
            try:
                return self.service._session_for_source(
                    item.source_provider, item.source_credential,
                ) is session
            except Exception:
                return False

        def work():
            # The task runner may start after the user changes identity,
            # credential or provider session; do not send a stale borrow POST.
            if not current():
                from ..plugin_api import Cancelled
                raise Cancelled("Le contexte de l'emprunt a changé.")
            loan = session.borrow(item, account)
            loan.identity_id = loan.identity_id or identity_id
            if loan.source_account_id is None:
                loan.source_account_id = account.unique_id or "primary"
            loans = self.service.refresh_identity_loans(force=True, identity_id=identity_id)
            return loan, loans

        def ok(value) -> None:
            if not current():
                return
            loan, _loans = value
            self._activity_result(f"Emprunt confirmé : {loan.title}")
            self.refresh_library(False)
            self.refresh_recent()

        self.runner.submit(f"Emprunt depuis {item.source_provider}…", work, ok, control=control,
            priority=TaskPriority.USER_ACTION, kind="borrow")

    def download_direct_item(self, item: SearchResult, control: Gtk.Widget | None = None) -> None:
        from .search_snapshot_refresh import invalidate_search_snapshot
        identity_id = getattr(self.service.current_identity, 'identity_id', None)
        generation = self._identity_context_generation
        def current():
            return (not getattr(self, '_closed', False)
                    and identity_id == getattr(self.service.current_identity, 'identity_id', None)
                    and generation == self._identity_context_generation)
        def work():
            if not current():
                from ..plugin_api import Cancelled
                raise Cancelled('Le contexte de l’obtention a changé.')
            return self.service.obtain_search_item(item)

        def ok(result) -> None:
            if not current():
                return
            acquisition = result.acquisition
            if result.path is not None:
                self._activity_result(f"Disponible dans la Bibliothèque : {result.path.name}")
                self.refresh_library(False)
                self.refresh_recent()
                invalidate_search_snapshot(self)
            elif acquisition.action == "command":
                self._activity_result("Commande d’obtention terminée")
            else:
                self._activity_result("Action d’obtention terminée")

        self.runner.submit(
            f"Obtention depuis {item.source_provider}…",
            work, ok, control=control,
            priority=TaskPriority.USER_ACTION, kind="obtain",
        )

    def open_downloads_folder(self) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(self.service.default_downloads_dir().as_uri(), None)
        except Exception as exc:
            self.show_error(str(exc), "Impossible d'ouvrir le dossier")

    def open_library_folder(self) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(self.service.library_dir().as_uri(), None)
        except Exception as exc:
            self.show_error(str(exc), "Impossible d'ouvrir la bibliothèque locale")

    # ---------- settings / diagnostics ----------
    def _build_settings_page(self, standalone: bool = False) -> Gtk.Widget:
        return settings_page.build_settings_page(
            self, standalone=standalone, Gtk=Gtk, Adw=Adw, Gdk=Gdk,
            TaskPriority=TaskPriority, AdaptiveActionBar=AdaptiveActionBar,
            action_button=action_button, add_css=add_css, clear_box=clear_box,
            mirror_sensitivity=mirror_sensitivity,
        ).scroll

    def refresh_diagnostics_text(self) -> None:
        cb = getattr(self, "_diag_refresh", None)
        if cb:
            cb()
