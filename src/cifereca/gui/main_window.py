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
from .settings_context import settings_action_state
from .paris_adept_presentation import normal_paris_adept_summary
from .settings_model import SettingsCommitController, SettingsRevisionState
from .settings_draft import build_settings_draft, SettingsValidationError
from .diagnostic_presentation import diagnostic_plugin_rows
from . import loans_controller, sync_controller, search_results, search_details, search_controller, library_refresh, navigation_controller, task_activity_adapter, shell_page_controller, library_navigation, header_action_controller, plugin_reload_controller, library_render_controller, library_page_controller, library_detail_controller
from .search_state import SearchStateField
from .settings_action_bars import build_settings_action_bars
from .settings_sync_section import build_kobo_sync_section, kobo_backup_action_state
from .settings_accounts_section import (build_identity_settings_header, build_provider_accounts_header, build_provider_account_buttons)
from .settings_application_interface import build_settings_application_interface
from .settings_account_editor import build_provider_account_editor
from .settings_identity_editor import build_identity_settings_editor
from .settings_library_downloads import build_library_download_section
from .settings_ai_section import build_ai_settings_section
from .settings_ai_actions import bind_settings_ai_callbacks
from .settings_diagnostics_section import build_settings_diagnostics_section
from .settings_shell import build_settings_shell
from .settings_lifetime import SettingsPageLifetime
from .settings_tracking import bind_settings_actions
from .settings_diagnostic_actions import build_settings_diagnostic_callbacks
from .window_close import close_window_resources
from .startup_page import build_startup_page
from .timeout_policy import bounded_seconds
from .startup_lifecycle import once_while_open
from . import startup_coordinator
from . import feedback_timers
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
from .maintenance_tools import preview_maintenance_tool, execute_maintenance_tool, MaintenanceCancelControl, MaintenanceCancelledError
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

from .action_widgets import clear_box, add_css, action_button


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

        self.notification_revealer = Gtk.Revealer()
        self.notification_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.notification_revealer.set_reveal_child(False)
        notification_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_css(notification_bar, "notification-bar")
        self.notification_label = Gtk.Label(label="", xalign=0)
        self.notification_label.set_hexpand(True)
        self.notification_label.set_wrap(True)
        notification_bar.append(self.notification_label)
        self.notification_close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.notification_close.set_tooltip_text("Fermer la notification")
        self.notification_close.connect("clicked", lambda _button: self._dismiss_notification())
        notification_bar.append(self.notification_close)
        self.notification_revealer.set_child(notification_bar)
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
        css = b"""
        .login-card { background: @card_bg_color; border-radius: 16px; padding: 28px; }
        .sidebar { background: @sidebar_bg_color; border-right: 1px solid @borders; }
        .sidebar row { border-radius: 8px; margin: 2px 8px; padding: 4px; }
        .sidebar row:selected { background: @accent_bg_color; color: @accent_fg_color; }
        .book-row { border-radius: 10px; }
        .book-row:hover { background: alpha(currentColor, 0.055); }
        .book-title { font-weight: 650; font-size: 1.05em; }
        .page-title { font-weight: 750; font-size: 1.55em; }
        .detail-title { font-weight: 800; font-size: 1.7em; }
        .detail-subtitle { font-size: 1.08em; }
        .badge { border-radius: 999px; padding: 3px 8px; background: alpha(currentColor, 0.10); font-size: 0.88em; }
        .book-tag { border-radius: 10px; padding: 2px 6px; font-size: 0.88em; }
        .tag-content { background: alpha(@accent_color, 0.14); font-weight: 600; }
        .tag-form { background: alpha(@success_color, 0.14); }
        .book-tag:focus { outline: 2px solid @accent_color; }
        .facet-value { border-radius: 8px; padding: 2px 6px; background: alpha(@accent_color, 0.08); }
        .facet-value:hover { background: alpha(@accent_color, 0.16); }
        .facet-value:focus { outline: 2px solid @accent_color; }
        .success-badge { background: alpha(@success_color, 0.20); color: @success_color; }
        .cover-frame { border-radius: 7px; background: alpha(currentColor, 0.06); }
        .busy-bar { background: @headerbar_bg_color; border-top: 1px solid @borders; padding: 8px 14px; }
        .notification-bar { background: @headerbar_bg_color; border-top: 1px solid @borders; padding: 7px 12px; }
        .settings-tabs button { padding-left: 8px; padding-right: 8px; min-width: 0; }
        .activity-success { color: @success_color; }
        .activity-error { color: @error_color; }
        .loan-overdue { background: alpha(@error_color, 0.14); color: @error_color; font-weight: 650; }
        .loan-imminent { background: alpha(@warning_color, 0.14); color: @warning_color; font-weight: 650; }
        .loan-normal { opacity: 0.85; }
        .metric { font-size: 1.35em; font-weight: 700; }
        .login-brand { font-size: 2.1em; font-weight: 800; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

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
        display_message = self._functional_error_message(message)
        self.toast(display_message, 6)
        dialog = Adw.MessageDialog.new(self, title, display_message)
        dialog.add_response("ok", "Fermer")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present()

    def show_attention(self, message: str, *, code: str = "attention_required") -> None:
        """Present a remediable condition without framing it as a crash."""
        if getattr(self, "_closed", False):
            return
        dialog = Adw.MessageDialog.new(self, "Action requise", message)
        dialog.add_response("close", "Fermer")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.set_body_use_markup(False)
        dialog.present()
        self.diagnostics.add(
            "UX", "attention required", "warning",
            detail={"code": code, "message": message}, level="detailed",
        )

    def _cancel_activity_hide(self) -> None:
        return feedback_timers.cancel_activity_hide(self, source_remove=GLib.source_remove)

    def _refresh_activity_history(self) -> None:
        if getattr(self, "_closed", False):
            return
        if not hasattr(self, "activity_history_box"):
            return
        clear_box(self.activity_history_box)
        rows = activity_history_rows(self.activity_feedback.rows())
        for display_text, is_error in rows:
            label = Gtk.Label(label=display_text, xalign=0)
            label.set_wrap(True)
            label.add_css_class("caption")
            if is_error:
                label.add_css_class("warning")
            self.activity_history_box.append(label)
        self.activity_history_button.set_visible(bool(rows))

    def _show_pending_activity_error(self) -> bool:
        if getattr(self, "_closed", False):
            return False
        entry = self.activity_feedback.latest_unacknowledged_error()
        if entry is None or not hasattr(self, "busy_revealer"):
            return False
        self.busy_spinner.stop(); self.busy_spinner.set_visible(False)
        self.activity_status_icon.set_visible(True)
        self.activity_status_icon.set_from_icon_name("dialog-warning-symbolic")
        self.activity_status_icon.add_css_class("activity-error")
        self.activity_status_icon.remove_css_class("activity-success")
        self.busy_label.set_visible(True)
        self.busy_label.set_label(entry.display_text)
        self.activity_dismiss.set_visible(True)
        self.busy_revealer.set_reveal_child(True)
        return True

    def _hide_activity_feedback(self) -> bool:
        self._activity_hide_source = None
        if getattr(self, "_closed", False) or self._active_tasks:
            return False
        pending_error = self._show_pending_activity_error()
        if not can_reduce_activity(
            closed=False, active_count=0, pending_error=pending_error,
            has_view=hasattr(self, "busy_revealer"),
        ):
            return False
        self.busy_spinner.stop(); self.busy_spinner.set_visible(False)
        self.activity_status_icon.set_visible(False)
        self.activity_dismiss.set_visible(False)
        rows = self.activity_feedback.rows()
        self.busy_label.set_visible(False)
        self.activity_history_button.set_visible(bool(rows))
        self.busy_revealer.set_reveal_child(bool(rows))
        return False

    def _dismiss_activity_feedback(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._cancel_activity_hide()
        self.activity_feedback.acknowledge_errors()
        self._refresh_activity_history()
        self._hide_activity_feedback()

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
        if getattr(self, "_closed", False):
            return
        resolved_id = int(task_id) if task_id is not None else self._last_finished_task_id
        if resolved_id is None:
            return
        self.activity_feedback.result(resolved_id, str(message), success=bool(success))
        self._refresh_activity_history()
        if self._active_tasks or not hasattr(self, "busy_revealer"):
            return
        if not success:
            self._show_pending_activity_error()
            return
        # A later success must never mask an earlier error that the user has
        # not acknowledged yet.
        if self._show_pending_activity_error():
            return
        self.busy_spinner.stop(); self.busy_spinner.set_visible(False)
        self.activity_status_icon.set_visible(True)
        self.activity_status_icon.set_from_icon_name("emblem-ok-symbolic")
        self.activity_status_icon.add_css_class("activity-success")
        self.activity_status_icon.remove_css_class("activity-error")
        self.busy_label.set_visible(True)
        self.busy_label.set_label(f"Terminé · {message}")
        self.activity_dismiss.set_visible(bool(persistent))
        self.busy_revealer.set_reveal_child(True)
        if not persistent:
            self._schedule_activity_reduce()

    def _task_started(self, task_id: int, count: int, label: str, priority: int, kind: str) -> bool:
        if getattr(self, '_closed', False):
            return False
        return task_activity_adapter.task_started(self, task_id, count, label, priority, kind)

    def _task_progress(self, label: str) -> bool:
        if getattr(self, '_closed', False):
            return False
        return task_activity_adapter.task_progress(self, label)

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
        return close_window_resources(self, source_remove=GLib.source_remove)

    # ---------- provider startup ----------
    def _build_startup(self) -> Gtk.Widget:
        return build_startup_page(self)

    def connect_configured_sources(self) -> bool:
        if getattr(self, "_closed", False):
            return False
        return startup_coordinator.connect_configured_sources(self, idle_add=GLib.idle_add)

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
        win = Adw.Window(transient_for=self, modal=False)
        win.set_title("Diagnostic / Configuration")
        win.set_default_size(900, 650)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Configuration et diagnostic"))
        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.set_tooltip_text("Fermer")
        close.connect("clicked", lambda _b: win.close())
        header.pack_end(close)
        content.append(header)
        page = self._build_settings_page(standalone=True)
        page.set_vexpand(True)
        content.append(page)
        win.set_content(content)

        keys = Gtk.EventControllerKey()
        def on_key(_controller, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                win.close()
                return True
            return False
        keys.connect("key-pressed", on_key)
        win.add_controller(keys)
        # Garder une référence explicite pendant toute la vie de la fenêtre.
        self._standalone_settings_window = win
        def standalone_closed(_window):
            if getattr(self, "_standalone_settings_window", None) is win:
                standalone_lifetime = self._settings_standalone_lifetime
                if standalone_lifetime is not None:
                    standalone_lifetime.dispose()
                    self._settings_standalone_lifetime = None
                self._standalone_settings_window = None
            return False
        win.connect("close-request", standalone_closed)
        win.present()

    # ---------- shell / navigation ----------
    def _build_main_shell(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.shell_root = root
        from .navigation_history import NavigationHistory
        self._navigation_history = NavigationHistory()
        self._restoring_navigation = False
        self.shell_header = self._create_shell_header()
        root.append(self.shell_header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.set_vexpand(True)
        root.append(body)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.sidebar = sidebar
        sidebar.set_size_request(160, -1)
        add_css(sidebar, "sidebar")
        body.append(sidebar)
        brand = Gtk.Label(label=f"v{__version__}", xalign=0)
        brand.set_margin_top(14); brand.set_margin_start(16); brand.set_margin_bottom(4)
        add_css(brand, "caption", "dim-label")
        sidebar.append(brand)
        self.nav_list = Gtk.ListBox()
        self.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav_list.set_activate_on_single_click(True)
        self.nav_list.connect("row-selected", self._nav_selected)
        sidebar.append(self.nav_list)
        self.nav_rows: dict[str, Gtk.ListBoxRow] = {}
        self._nav_groups = {}
        self._nav_group_expanded = {}
        self.view_stack = Gtk.Stack()
        self.view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.view_stack.set_hhomogeneous(False)
        self.view_stack.set_hexpand(True); self.view_stack.set_vexpand(True)
        body.append(self.view_stack)
        self._rebuild_plugin_pages()

        self.busy_revealer = Gtk.Revealer()
        self.busy_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        busy = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        add_css(busy, "busy-bar")
        self.busy_spinner = Gtk.Spinner(); self.busy_spinner.set_visible(False); busy.append(self.busy_spinner)
        self.activity_status_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        self.activity_status_icon.set_visible(False); busy.append(self.activity_status_icon)
        self.busy_label = Gtk.Label(label="", xalign=0)
        self.busy_label.set_hexpand(True); self.busy_label.set_wrap(True); busy.append(self.busy_label)

        self.activity_history_button = Gtk.MenuButton(label="Historique")
        self.activity_history_button.set_icon_name("view-list-symbolic")
        self.activity_history_button.set_tooltip_text("Historique des opérations")
        self.activity_history_button.set_visible(False)
        history_popover = Gtk.Popover()
        self.activity_history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.activity_history_box.set_margin_top(8); self.activity_history_box.set_margin_bottom(8)
        self.activity_history_box.set_margin_start(10); self.activity_history_box.set_margin_end(10)
        history_popover.set_child(self.activity_history_box)
        self.activity_history_button.set_popover(history_popover)
        busy.append(self.activity_history_button)

        self.activity_dismiss = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.activity_dismiss.set_tooltip_text("Acquitter le résultat courant")
        self.activity_dismiss.set_visible(False)
        self.activity_dismiss.connect("clicked", lambda _button: self._dismiss_activity_feedback())
        busy.append(self.activity_dismiss)
        root.append(self.busy_revealer); self.busy_revealer.set_child(busy)

        self.navigate("dashboard")
        return root

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
        if route in self.nav_rows:
            return
        row = Gtk.ListBoxRow(); row.set_name(route)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        line.set_margin_top(7); line.set_margin_bottom(7); line.set_margin_start(8); line.set_margin_end(8)
        icon = Gtk.Image.new_from_icon_name(icon_name); icon.set_pixel_size(18); line.append(icon)
        text = Gtk.Label(label=label, xalign=0); text.set_hexpand(True); line.append(text)
        row.set_child(line); self.nav_list.append(row); self.nav_rows[route] = row

    def _build_standard_page(self, route):
        builders = {"dashboard": self._build_dashboard_page, "loans": self._build_loans_page,
                    "search": self._build_search_page, "history": self._build_history_page,
                    "bookings": self._build_bookings_page, "library": self._build_library_page,
                    "recent": self._build_recent_page, "sync": self._build_sync_page,
                    "tools": self._build_tools_page, "settings": self._build_settings_page,
                    "plugins": lambda: PluginManagerPage(self, self.plugin_manager, self._plugins_changed)
                    if self.plugin_manager else Gtk.Label(label="Gestionnaire indisponible")}
        widget = builders[route]()
        attr = {"loans": "loan", "bookings": "booking"}.get(route, route)
        setattr(self, attr + "_page", widget)
        if route == "library": self.kobo_page = widget
        return widget

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
        from .shortcuts import SHORTCUTS
        callbacks = {
            "quit": self._quit_application,
            "close": self.close,
            "pages-menu": self._open_pages_menu,
            "focus-search": self._focus_search,
            "settings": self._show_settings,
            "next-page": lambda: self._cycle_page(1),
            "previous-page": lambda: self._cycle_page(-1),
            "go-back": lambda: getattr(self, "go_back", lambda: None)(),
            "go-forward": lambda: getattr(self, "go_forward", lambda: None)(),
            "shortcuts": self._show_shortcuts,
            "reconnect-sources": self.reconnect_sources,
        }
        for name, callback in callbacks.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
        for name, keys, _description in SHORTCUTS:
            self.get_application().set_accels_for_action("win." + name, list(keys))

    def _quit_application(self):
        # Close through the normal handler before application shutdown.
        application = self.get_application()
        self.close()
        if application is not None:
            application.quit()

    def _open_pages_menu(self):
        button = getattr(self, "pages_menu_button", None)
        if button is not None:
            button.popup()

    def _focus_search(self):
        if not hasattr(self, "_pages"):
            return
        route = self.view_stack.get_visible_child_name()
        if route not in {"search", "library"}:
            if "search" not in self._pages:
                return
            self.navigate("search"); route = "search"
        controls = getattr(self, route + "_controls", None)
        if controls is not None:
            controls.entry.grab_focus()

    def _show_settings(self):
        if "settings" in getattr(self, "_pages", {}):
            self.navigate("settings")
        else:
            self._open_prelogin_diagnostics()

    def _cycle_page(self, direction):
        from .shortcuts import adjacent_route
        routes = list(getattr(self, "_pages", {}))
        if routes:
            self.navigate(adjacent_route(routes, self.view_stack.get_visible_child_name(), direction))

    def _show_shortcuts(self):
        from .shortcuts import shortcuts_text
        dialog = Adw.MessageDialog.new(self, "Raccourcis clavier", shortcuts_text())
        dialog.add_response("close", "Fermer"); dialog.present()

    def _create_shell_header(self):
        host = self._UiHost(self)
        self._titlebar_service = None
        if self.plugin_manager:
            try:
                selected = self.plugin_manager.select("ui.titlebar")
                self._titlebar_service = self.plugin_manager.service(selected.descriptor.id) if selected else None
            except Exception:
                self._titlebar_service = None
        try:
            header = self._titlebar_service.build(host) if self._titlebar_service else Adw.HeaderBar()
            if not isinstance(header, Adw.HeaderBar): raise TypeError("Titlebar must return Adw.HeaderBar")
        except Exception as exc:
            self.diagnostics.add("PLUGIN", "titlebar build", "error", detail={"error": str(exc)})
            header = Adw.HeaderBar(); self._titlebar_service = None
        self.header_title = Gtk.Label(label="cifereca")
        self.header_subtitle = Gtk.Label(label="")
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title.append(self.header_title); title.append(self.header_subtitle); header.set_title_widget(title)
        navigation = Gtk.Box(spacing=2)
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_button.set_tooltip_text("Précédent (Alt+Gauche)")
        self.back_button.connect("clicked", lambda _b: self.go_back())
        self.forward_button = Gtk.Button(icon_name="go-next-symbolic")
        self.forward_button.set_tooltip_text("Suivant (Alt+Droite)")
        self.forward_button.connect("clicked", lambda _b: self.go_forward())
        navigation.append(self.back_button); navigation.append(self.forward_button)
        header.pack_start(navigation)
        self.header_actions = Gtk.Box(spacing=4); header.pack_start(self.header_actions)
        updater = getattr(self, "_update_navigation_buttons", None)
        if callable(updater): updater()
        def compact_dropdown(tooltip: str) -> Gtk.DropDown:
            dropdown = Gtk.DropDown()
            factory = Gtk.SignalListItemFactory()
            def setup(_factory, list_item):
                label = Gtk.Label(xalign=0)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_single_line_mode(True)
                list_item.set_child(label)
            def bind(_factory, list_item):
                item = list_item.get_item()
                label = list_item.get_child()
                text = item.get_string() if isinstance(item, Gtk.StringObject) else str(item or "")
                label.set_label(text)
                label.set_tooltip_text(text)
            factory.connect("setup", setup)
            factory.connect("bind", bind)
            dropdown.set_factory(factory)

            list_factory = Gtk.SignalListItemFactory()
            def setup_list(_factory, list_item):
                label = Gtk.Label(xalign=0)
                label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                label.set_single_line_mode(True)
                label.set_max_width_chars(44)
                list_item.set_child(label)
            list_factory.connect("setup", setup_list)
            list_factory.connect("bind", bind)
            dropdown.set_list_factory(list_factory)
            dropdown.set_tooltip_text(tooltip)
            return dropdown

        self.identity_dropdown = compact_dropdown("Identité active")
        self.identity_dropdown.connect("notify::selected", self._identity_changed)
        self.account_dropdown = compact_dropdown("Délégations de l’identité active")
        self.account_dropdown.connect("notify::selected", self._account_changed)
        self.pages_menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.pages_menu_button.set_tooltip_text("Menu des pages (F10)")
        self.shell_menu = Gtk.PopoverMenu.new_from_model(Gio.Menu())
        self.pages_menu_button.set_popover(self.shell_menu)
        # One ordered box makes the menu the rightmost application control.
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.statistics_button=Gtk.Button(label="— livres · — prêts")
        self.statistics_button.add_css_class('flat')
        self.statistics_button.set_tooltip_text("Statistiques de l’identité : inventaire local et derniers prêts connus")
        self.statistics_button.connect('clicked',lambda _b:self._show_statistics())
        controls.append(self.identity_dropdown); controls.append(self.account_dropdown)
        controls.append(self.statistics_button)
        controls.append(self.pages_menu_button)
        self._header_controls = controls
        header.pack_end(controls)
        self._ensure_pages_menu_rightmost()
        return header

    def _ensure_pages_menu_rightmost(self):
        controls = getattr(self, "_header_controls", None)
        menu = getattr(self, "pages_menu_button", None)
        if controls is None or menu is None:
            return
        children = getattr(controls, "children", None)
        if isinstance(children, list):
            if menu in children:
                children.remove(menu)
            children.append(menu)
            return
        get_last = getattr(controls, "get_last_child", None)
        if callable(get_last) and get_last() is menu:
            return
        get_parent = getattr(menu, "get_parent", None)
        if callable(get_parent) and get_parent() is controls:
            controls.remove(menu)
        controls.append(menu)

    def _refresh_header_actions(self, route):
        return header_action_controller.refresh_header_actions(
            self, route, Gtk=Gtk, Gio=Gio, clear_box=clear_box,
            validate_action_selection=validate_action_selection,
            visible_actions=visible_actions, menu_sections=menu_sections,
        )

    def _invoke_ui_action(self, action, host):
        self.shell_menu.popdown()
        try: action.callback(host)
        except Exception as exc: self.show_error(str(exc), "Action impossible")

    def _show_route(self, route: str) -> None:
        if route not in self._pages: return
        self.view_stack.set_visible_child_name(route)
        page = self._pages.get(route)
        self.header_subtitle.set_label(page.title if page else "")
        self._refresh_header_actions(route)
        widget = self._plugin_page_widgets.get(route)
        if widget and isinstance(widget.get_root(), Gtk.Window):
            widget.child_focus(Gtk.DirectionType.TAB_FORWARD)
        if route == "dashboard":
            self._render_dashboard()
        elif route == "library":
            self._render_library(self._library_entries_cache)
            if not self._library_entries_cache:
                self.refresh_library(False)
        elif route == "recent":
            self._render_recent(self._recent_entries_cache)
            if not self._recent_entries_cache:
                self.refresh_recent()
        elif route == "history" and not self.service.last_history:
            self.refresh_history(False)
        elif route == "bookings" and not self.service.last_bookings:
            self.refresh_bookings(False)
        elif route == "sync":
            self._refresh_sync_page()
        elif route == "tools":
            self._refresh_tools_page()
        elif route == "settings":
            self.refresh_diagnostics_text()
        elif route == "plugins" and hasattr(self.plugins_page, "refresh"):
            self.plugins_page.refresh()

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
        model = dropdown.get_model()
        if isinstance(model, Gtk.StringList):
            previous = [model.get_string(i) for i in range(model.get_n_items())]
            if previous != labels:model.splice(0, model.get_n_items(), labels)
        else:
            dropdown.set_model(Gtk.StringList.new(labels))

    def _populate_account_dropdown(self, identity_id: str | None) -> None:
        self._account_changing = True
        try:
            public = {a.provider_id for a in self.service.accounts
                      if getattr(getattr(self.service._provider_plugin_status(a.provider_id), "descriptor", None), "instantiation", "multiple") == "singleton_public"}
            self._visible_accounts = delegated_accounts(self.service.accounts, identity_id, public)
            self.account_dropdown.set_visible(bool(self._visible_accounts))
            labels = [
                f"{a.provider_label} · {a.display_name or ('Compte principal' if a.is_primary else 'Compte lié')}"
                for a in self._visible_accounts
            ]
            self._replace_dropdown_labels(self.account_dropdown, labels)
            current = self.service.current_account
            account_index = next((i for i, account in enumerate(self._visible_accounts) if account is current), Gtk.INVALID_LIST_POSITION)
            self.account_dropdown.set_selected(account_index if self._visible_accounts else Gtk.INVALID_LIST_POSITION)
        finally:
            self._account_changing = False

    def _populate_accounts(self) -> None:
        self._identity_changing = True
        try:
            identities = list(self.service.identities)
            self._replace_dropdown_labels(self.identity_dropdown, [identity.label for identity in identities])
            selected_identity = self.service.current_identity.identity_id if self.service.current_identity else None
            selected_index = next((i for i, identity in enumerate(identities) if identity.identity_id == selected_identity), 0)
            self.identity_dropdown.set_selected(selected_index if identities else Gtk.INVALID_LIST_POSITION)
        finally:
            self._identity_changing = False

        self._populate_account_dropdown(selected_identity)
        if hasattr(self,'_refresh_statistics'):self._refresh_statistics()

    def _identity_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        if self._identity_changing:
            return
        idx = dropdown.get_selected()
        if idx < 0 or idx >= len(self.service.identities):
            return
        identity = self.service.identities[idx]
        self._activate_identity(identity.identity_id)

    def open_identity_library(self, identity_id: str) -> None:
        self._activate_identity(identity_id)
        self.navigate("library")

    def open_identity_view(self, identity_id: str, *, route: str, providers=()) -> None:
        from .query_model import QueryState
        if route not in {"search", "library"}:
            raise ValueError("Vue filtrée inconnue")
        if isinstance(providers, str) or any(not isinstance(p, str) or not p for p in providers):
            raise ValueError("Sources invalides")
        allowed = {d.id for d in self.service.provider_descriptors()} | {"local"}
        selected = tuple(dict.fromkeys(providers))
        if not set(selected) <= allowed:
            raise ValueError("Source inconnue")
        self._activate_identity(identity_id)
        controls = self.search_controls if route == "search" else self.library_controls
        controls.set_state(QueryState(scope="digital", providers=selected))
        self.navigate(route)
        if route == "search": self.run_search(False)
        else: self._render_library()

    def _activate_identity(self, identity_id: str) -> None:
        identity = self.service.identity_by_id(identity_id)
        if identity is None:
            raise ValueError("Identité inconnue")
        current = self.service.current_identity
        current_identity_id = current.identity_id if current is not None else None
        if not identity_change_required(current_identity_id, identity.identity_id):
            _TRACE_LOG.debug("Identity selection notification ignored: already current identity=%s", identity.identity_id)
            return
        self.service.select_identity(identity.identity_id)
        self._identity_context_generation += 1
        self._account_context_generation += 1
        self._cached_book_request_generation += 1
        self._cached_book_context = None
        # Pending Library navigation belongs to the previous identity context.
        # Cancel it before rendering the new identity's (initially empty) list.
        self._pending_library_selection_key = None
        self._pending_library_selection_identity_id = None
        self._pending_library_selection_generation = None
        identity_generation = self._identity_context_generation
        self._populate_accounts()
        self._render_dashboard()
        self._search_generation = getattr(self, "_search_generation", 0) + 1
        self._search_detail_request_generation += 1
        self._search_local_objects = {}; self.search_objects = {}; self._remote_search_results = []
        self._search_mode = "local"; self._remote_query_key = None; self._selected_search_id = None
        if hasattr(self, "search_page"):
            self.search_page.clear_rows()
            self.search_page.detail.empty("Nouvelle identité", "Lancez une recherche dans le connu.")
        # Do not rebuild the identity dropdown from its own notify::selected callback:
        # GTK may deliver the resulting notification asynchronously after the guard
        # is released, causing an unbounded refresh/task loop.
        self._populate_account_dropdown(identity.identity_id)
        self.service.last_history = []
        self.service.last_bookings = []
        self.history_objects = {}
        self.booking_objects = {}
        self._history_all_rows = []
        self._history_rows = []
        self._history_loaded_count = 0
        self._history_append_scheduled = False
        if hasattr(self, "history_page"):
            self.history_page.clear_rows()
        if hasattr(self, "booking_page"):
            self.booking_page.clear_rows()
        self._library_entries_cache = []
        self._recent_entries_cache = []
        self._recent_objects = {}
        if hasattr(self, "recent_page"):
            self.recent_page.clear_rows()
            self.recent_page.detail.empty("Nouvelle identité", "Chargement des récupérations…")
        if hasattr(self, "library_page"):
            self._render_library([])
            self.library_page.detail.empty("Nouvelle identité", "Chargement de la bibliothèque…")
        def identity_loaded(rows: list[Loan]) -> None:
            if not self.service.current_identity or self.service.current_identity.identity_id != identity.identity_id:
                return
            if not self._identity_request_current(identity.identity_id, identity_generation):
                return
            self._populate_loans(rows)
            self.refresh_library(False)
            self.refresh_recent()
            route = self.view_stack.get_visible_child_name() if hasattr(self, "view_stack") else None
            if route == "history":
                self.refresh_history(False)
            elif route == "bookings":
                self.refresh_bookings(False)
            self._activity_result(f"Identité chargée : {identity.label}")
        self.runner.submit(
            f"Chargement de {identity.label}…",
            lambda: self.service.refresh_identity_loans(force=False, identity_id=identity.identity_id),
            identity_loaded,
            priority=TaskPriority.USER_ACTION,
            kind="identity",
        )

    def _account_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        if self._account_changing:
            return
        idx = dropdown.get_selected()
        accounts = getattr(self, "_visible_accounts", [])
        if idx < 0 or idx >= len(accounts):
            return
        account = accounts[idx]
        current = self.service.current_account
        if current is not None and (
            current.unique_id == account.unique_id
            and current.provider_id == account.provider_id
            and current.credential_id == account.credential_id
        ):
            _TRACE_LOG.debug("Account selection notification ignored: already current account=%s", account.unique_id)
            return
        self.service.select_account(account.unique_id, account.provider_id, account.credential_id)
        self._account_context_generation += 1
        self.service.last_history = []
        self.service.last_bookings = []
        self.history_objects = {}
        self.booking_objects = {}
        self._history_all_rows = []
        self._history_rows = []
        self._history_loaded_count = 0
        self._history_append_scheduled = False
        if hasattr(self, "history_page"):
            self.history_page.show_rows([])
            self.history_page.detail.empty("Historique non chargé", "Ouvrez l’onglet Historique pour charger ce compte.")
        if hasattr(self, "booking_page"):
            self.booking_page.show_rows([])
            self.booking_page.detail.empty("Réservations non chargées", "Ouvrez l’onglet Réservations pour charger ce compte.")
        self.toast(f"Compte : {account.display_name or account.provider_label}")


    # ---------- 0.8.2 opening dashboard ----------
    def _build_dashboard_page(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        outer.set_margin_top(24); outer.set_margin_bottom(24); outer.set_margin_start(24); outer.set_margin_end(24)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); outer.append(heading)
        title = Gtk.Label(label="Bibliothèque familiale", xalign=0); title.set_hexpand(True); title.set_wrap(True); add_css(title, "page-title"); heading.append(title)
        subtitle = Gtk.Label(label="État des identités et de la synchronisation automatique", xalign=0); add_css(subtitle, "dim-label"); outer.append(subtitle)
        self.dashboard_loading_status = Gtk.Label(label="Connexion aux sources en arrière-plan…", xalign=0)
        self.dashboard_loading_status.set_wrap(True); add_css(self.dashboard_loading_status, "dim-label", "caption")
        outer.append(self.dashboard_loading_status)
        actions = AdaptiveActionBar(source=heading)
        refresh = self.action_button("Synchronisation rapide", "view-refresh-symbolic", suggested=True)
        refresh.connect("clicked", lambda _b: self.run_opening_sync(refresh, deep=False)); actions.append(refresh)
        deep = self.action_button("Vérifier toute la bibliothèque", "system-search-symbolic")
        deep.connect("clicked", lambda _b: self.run_opening_sync(deep, deep=True)); actions.append(deep)
        actions.set_halign(Gtk.Align.END); heading.append(actions)
        self.dashboard_cards = Gtk.FlowBox(); self.dashboard_cards.set_selection_mode(Gtk.SelectionMode.NONE)
        self.dashboard_cards.set_min_children_per_line(1); self.dashboard_cards.set_max_children_per_line(3)
        self.dashboard_cards.set_homogeneous(True); self.dashboard_cards.set_column_spacing(12); self.dashboard_cards.set_row_spacing(12)
        outer.append(self.dashboard_cards)
        scroll.set_child(outer)
        return scroll

    @staticmethod
    def _sync_status_label(status: str | None) -> str:
        return {"running": "En cours", "success": "Réussie", "partial": "Partielle", "failed": "Échouée"}.get(str(status or ""), "Jamais synchronisée")

    def _refresh_statistics(self):
        if not hasattr(self,'statistics_button'):return
        identity=self.service.current_identity
        if not identity:return
        stats=self.service.identity_statistics(identity.identity_id)
        loans=stats['known_loans']
        self.statistics_button.set_label(f"{stats['local_books']} livres · {loans if loans is not None else '—'} prêts")

    def _show_statistics(self):
        identity=self.service.current_identity
        if not identity:return
        stats=self.service.identity_statistics(identity.identity_id)
        loans=stats['known_loans']
        rows=[f"Livres locaux distincts : {stats['local_books']}",f"Emplacements locaux indexés présents : {stats['local_placements']}",
              f"Emprunts connus : {loans if loans is not None else 'non actualisés'}",f"Sources de prêts observées : {stats['loan_sources_observed']}",
              f"Droits d’achat distants actifs : {stats['remote_owned_rights']}"]
        for title,key in [('Formats','formats'),('Sources locales','sources'),('Couvertures EPUB','epub_covers')]:
            values=stats[key]
            rows.append(title+' : '+(', '.join(f'{name} {count}' for name,count in sorted(values.items())) or '—'))
        actions=stats.get('actions') or {}
        if actions:
            rows.append('Actions sur la période :')
            rows.append(f"  Acquisitions : {actions.get('acquisitions_succeeded',0)} réussies · {actions.get('acquisitions_failed',0)} échouées")
            rows.append(f"  Imports : {actions.get('imports_succeeded',0)} réussis · {actions.get('imports_failed',0)} échoués")
            rows.append(f"  Synchronisations : {actions.get('synchronizations_succeeded',0)} réussies · {actions.get('synchronizations_failed',0)} échouées")
            rows.append(f"  Transformations : {actions.get('transformations_succeeded',0)} réussies · {actions.get('transformations_failed',0)} échouées")
            rows.append(f"  Transferts : {actions.get('transfers_succeeded',0)} réussis · {actions.get('transfers_failed',0)} échoués")
            if actions.get('from_timestamp') and actions.get('to_timestamp'):
                rows.append('  Fenêtre : '+datetime.fromtimestamp(actions['from_timestamp']).strftime('%d/%m/%Y %H:%M')+' → '+datetime.fromtimestamp(actions['to_timestamp']).strftime('%d/%m/%Y %H:%M'))
        if stats.get('loans_observed_at'):
            rows.append("Prêts observés au plus tôt : "+datetime.fromtimestamp(stats['loans_observed_at']).strftime('%d/%m/%Y %H:%M'))
        rows.append("Inventaire issu du dernier scan ; prêts issus des dernières réponses disponibles, éventuellement partielles. Les statistiques d'actions utilisent la fenêtre indiquée.")
        dialog=Adw.MessageDialog.new(self,f"Statistiques · {identity.label}",'\n'.join(rows))
        dialog.add_response('close','Fermer');dialog.present()

    def _render_dashboard(self) -> None:
        if hasattr(self,'_refresh_statistics'):self._refresh_statistics()
        for callback in list(getattr(self, "_overview_listeners", [])):
            try: callback()
            except Exception as exc: self.diagnostics.add("UI", "identity overview callback", "error", detail={"error": type(exc).__name__})
        if not hasattr(self, "dashboard_cards"):
            return
        child = self.dashboard_cards.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling(); self.dashboard_cards.remove(child); child = nxt
        for card in self.service.dashboard_state():
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(14); box.set_margin_end(14)
            add_css(box, "cover-frame")
            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Button(label=str(card.get("label") or "Utilisateur")); name.set_hexpand(True)
            name.get_child().set_wrap(True)
            name.set_tooltip_text("Sélectionner cette identité et ouvrir sa bibliothèque")
            name.connect("clicked", lambda _b, iid=card["identity_id"]: self.open_identity_library(iid))
            add_css(name, "flat", "book-title"); head.append(name)
            sync = card.get("sync") or {}
            badge = Gtk.Label(label=self._sync_status_label(sync.get("status"))); add_css(badge, "badge"); head.append(badge)
            box.append(head)
            stats=card.get('statistics') or {}
            if stats:
                loans=stats.get('known_loans')
                count=Gtk.Label(label=f"{stats.get('local_books',0)} livres locaux · {loans if loans is not None else '—'} emprunts connus",xalign=0)
                count.set_wrap(True);box.append(count)
                actions=stats.get('actions') or {}
                if actions:
                    ok=sum(int(actions.get(key,0) or 0) for key in ('acquisitions_succeeded','imports_succeeded','synchronizations_succeeded','transformations_succeeded','transfers_succeeded'))
                    failed=sum(int(actions.get(key,0) or 0) for key in ('acquisitions_failed','imports_failed','synchronizations_failed','transformations_failed','transfers_failed'))
                    action_label=Gtk.Label(label=f"Actions (30 j) : {ok} réussies · {failed} échouées",xalign=0)
                    action_label.set_wrap(True); add_css(action_label, 'caption', 'dim-label'); box.append(action_label)
            providers = ", ".join(source["label"] + ("" if source["connected"] else " (à connecter)") for source in card.get("sources", [])) or "Aucune source connectée"
            source_label = Gtk.Label(label=f"Sources : {providers}", xalign=0); source_label.set_wrap(True); box.append(source_label)
            profile = "Lié" if card.get("adept_profile_id") else "À configurer"
            box.append(Gtk.Label(label=f"Profil Adobe : {profile}", xalign=0))
            deep_last_at = card.get("deep_last_at")
            if deep_last_at:
                try:
                    deep_label = datetime.fromtimestamp(int(deep_last_at)).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    deep_label = str(deep_last_at)
                lab = Gtk.Label(label=f"Dernière vérification complète : {deep_label}", xalign=0); add_css(lab, "caption", "dim-label"); box.append(lab)
            if sync:
                sync_detail = sync.get("detail") or {}
                provisioning = sync_detail.get("adept_provisioning") or {}
                if provisioning.get("state"):
                    box.append(Gtk.Label(label=f"Provisioning : {provisioning.get('state')}", xalign=0))
                details = f"Nouvelles acquisitions : {sync.get('new_acquisitions', 0)} · Actions : {sync.get('attention_required', 0)} · Erreurs : {sync.get('errors', 0)}"
                lab = Gtk.Label(label=details, xalign=0); lab.set_wrap(True); add_css(lab, "caption", "dim-label"); box.append(lab)
                if sync.get("errors") or sync.get("attention_required"):
                    issues = Gtk.Button(label="Voir les erreurs et actions")
                    issues.connect("clicked", lambda _b, c=card: self._show_sync_issues(c)); box.append(issues)
            box.set_size_request(300, -1)
            for capability in card.get("capabilities", []):
                label = Gtk.Label(label="✓ " + capability["label"], xalign=0); add_css(label, "caption")
                box.append(label)
            self.dashboard_cards.insert(box, -1)


    def _choose_identity_directory(self, entry, root: Path) -> None:
        from ..identities.directory_selection import directory_choice
        chooser = Gtk.FileChooserNative.new("Choisir un dossier d’identité", self,
                     Gtk.FileChooserAction.SELECT_FOLDER, "Choisir", "Annuler")
        chooser.set_current_folder(Gio.File.new_for_path(str(root.expanduser())))
        self._identity_directory_chooser = chooser
        def response(dialog, selected):
            try:
                if selected == Gtk.ResponseType.ACCEPT:
                    file = dialog.get_file()
                    if file is None or not file.get_path():
                        raise ValueError("Choisissez un dossier local.")
                    entry.set_text(directory_choice(file.get_path(), root))
            except Exception as exc:
                self.show_error(str(exc), "Choix du répertoire impossible")
            finally:
                dialog.destroy()
                if getattr(self, "_identity_directory_chooser", None) is dialog:
                    self._identity_directory_chooser = None
        chooser.connect("response", response); chooser.show()

    def _choose_adept_profile(self, entry, control) -> None:
        def done(choices):
            if not choices:
                self.show_error("Autorisez un profil Adobe capable de récupérer les ACSM dans Kobo-Adept, puis réessayez.", "Aucun profil ADE prêt")
                return
            dialog = Adw.MessageDialog.new(self, "Choisir le profil ADE", "Choisissez le profil Adobe correspondant aux prêts de cette identité. Enregistrez ensuite les paramètres, puis relancez la synchronisation.")
            references = {}
            for index, choice in enumerate(choices):
                response = "profile-" + str(index)
                references[response] = choice["reference"]
                dialog.add_response(response, choice["name"] + " · " + choice["reference"])
            dialog.add_response("cancel", "Annuler")
            dialog.set_close_response("cancel")
            def selected(_dialog, response):
                if response in references:
                    entry.set_text(references[response])
            dialog.connect("response", selected)
            dialog.present()
        self.runner.submit("Profils ADE…", self.service.adept_profile_choices, done,
                           lambda exc: self.show_error(str(exc), "Profils ADE"), control=control)

    def _show_sync_issues(self, card):
        from .sync_issues import sync_issues_text
        dialog = Adw.MessageDialog.new(self, "Synchronisation · " + str(card.get("label") or ""), sync_issues_text(card.get("sync") or {}))
        dialog.add_response("settings", "Paramètres des identités")
        dialog.add_response("close", "Fermer")
        def response(_dialog, selected):
            if selected == "settings":
                self.navigate("settings")
                self._settings_tabs.set_visible_child_name("accounts")
        dialog.connect("response", response)
        dialog.present()

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

    def _refresh_tools_page(self) -> None:
        box = getattr(self, "_tools_box", None)
        if box is None: return
        clear_box(box)
        services = self.plugin_manager.services("maintenance.tool") if self.plugin_manager else ()
        if not services:
            label = Gtk.Label(label="Aucun outil de maintenance additionnel n’est installé.", xalign=0)
            add_css(label, "dim-label"); box.append(label); return
        for resolved in services:
            tool = resolved.service
            try: info = tool.describe()
            except Exception as exc:
                box.append(Gtk.Label(label=f"{resolved.plugin_id}: description indisponible ({exc})", xalign=0)); continue
            group = Adw.PreferencesGroup(); box.append(group)
            row = Adw.ActionRow(); row.set_title(info.title); row.set_subtitle(info.description or resolved.plugin_id)
            run = self.action_button("Prévisualiser…" if info.supports_dry_run else "Exécuter…", info.icon_name)
            row.add_suffix(run)
            cancel = Gtk.Button(label="Annuler")
            cancel.set_visible(False)
            row.add_suffix(cancel)
            group.add(row)
            active_cancel = {"control": None}

            def request_cancel(_cancel_button, cancellation_state=active_cancel):
                active = cancellation_state["control"]
                if active is not None:
                    active.cancel()
                    _cancel_button.set_sensitive(False)

            cancel.connect("clicked", request_cancel)

            def prepare(
                _button, service=tool, meta=info,
                cancel_button=cancel, cancellation_state=active_cancel,
            ):
                identity = self.service.current_identity
                context = MaintenanceContext(scope=meta.scope, identity_id=identity.identity_id if identity else None)
                destructive = meta.danger_level == "destructive"
                # Planning is itself cancellable from GTK. The plugin plan()
                # signature has no control, so cancellation discards its result;
                # it must never reopen a stale confirmation after a slow scan.
                previous_control = cancellation_state["control"]
                if previous_control is not None:
                    previous_control.cancel()
                planning_control = MaintenanceCancelControl(
                    owner_closed=lambda: self._closed,
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
                    current_identity = self.service.current_identity
                    if (current_identity.identity_id if current_identity else None) != context.identity_id:
                        self.show_error(
                            "L’identité active a changé. Relancez la prévisualisation.",
                            meta.title,
                        )
                        return
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
                        if selected != "run" or self._closed:
                            return
                        current_identity = self.service.current_identity
                        if (current_identity.identity_id if current_identity else None) != context.identity_id:
                            self.show_error(
                                "L’identité active a changé. Relancez la prévisualisation.",
                                meta.title,
                            )
                            return

                        worker_control = MaintenanceCancelControl(
                            owner_closed=lambda: self._closed,
                        )
                        cancellation_state["control"] = worker_control
                        can_cancel = getattr(meta, "cancellable", False) is True
                        cancel_button.set_visible(can_cancel)
                        cancel_button.set_sensitive(can_cancel)

                        def finish_maintenance() -> None:
                            if cancellation_state["control"] is worker_control:
                                cancellation_state["control"] = None
                            cancel_button.set_visible(False)

                        def completed(result) -> None:
                            finish_maintenance()
                            self._activity_result(
                                result.summary or ("Outil terminé" if result.ok else "Outil terminé avec erreurs"),
                                success=bool(result.ok),
                                persistent=not bool(result.ok),
                            )

                        def failed(exc) -> None:
                            finish_maintenance()
                            if isinstance(exc, MaintenanceCancelledError):
                                self.show_error(
                                    "Traitement interrompu ; les modifications déjà effectuées ne sont pas annulées.",
                                    meta.title,
                                )
                            else:
                                self.show_error(str(exc), meta.title)

                        # Plan revalidation and mutations hold the Core lock.
                        # The separate cancellation button sets a thread-safe
                        # event; each plugin check_cancelled() observes it.
                        self.runner.submit(
                            meta.title,
                            lambda: execute_maintenance_tool(
                                service, context, plan, worker_control, destructive=destructive,
                            ),
                            completed,
                            failed,
                            control=_button,
                            priority=TaskPriority.MAINTENANCE,
                            kind="maintenance-tool",
                        )
                    dialog.connect("response", response); dialog.present()

                # Planning can scan thousands of placements. Never call
                # service.plan(context) on the main GTK event loop.
                self.runner.submit(
                    f"Analyse : {meta.title}",
                    lambda: preview_maintenance_tool(
                        service, context, destructive=destructive, control=planning_control,
                    ),
                    present_preview,
                    lambda exc: (
                        self.show_error(str(exc), meta.title)
                        if finish_preview() and not planning_control.cancelled()
                        and not self._closed and _button.get_root() is not None
                        else None
                    ),
                    control=_button,
                    priority=TaskPriority.MAINTENANCE,
                    kind="maintenance-plan",
                )
            run.connect("clicked", prepare)

    def run_opening_sync(self, control: Gtk.Widget | None = None, *, deep: bool = False) -> None:
        return sync_controller.run_opening_sync(self, control, deep=deep)

    def _known_item_languages(self, provider_id: str, item_id: str | None, metadata: dict[str, Any] | None = None) -> set[str]:
        from cifereca.language import normalize_language_tag
        values = []
        meta = dict(metadata or {})
        raw = meta.get('languages') or meta.get('language') or ()
        values.extend(raw if isinstance(raw, (list, tuple, set)) else (raw,))
        if item_id:
            for row in self.service.storage.catalog_entries():
                if str(row.get('provider_id') or '') != str(provider_id or ''): continue
                if str(row.get('item_id') or '') != str(item_id): continue
                row_meta = dict(row.get('metadata') or {})
                raw = row_meta.get('languages') or row_meta.get('language') or ()
                values.extend(raw if isinstance(raw, (list, tuple, set)) else (raw,))
        return {tag for value in values if (tag := normalize_language_tag(value))}

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
        providers = dict(self.service.searchable_providers())
        providers["local"] = "Local"
        for row in self.service.catalog_entries():
            provider = str(row.get("provider_id") or "local")
            providers.setdefault(provider, self.service.provider_display_name(provider))
        providers = sorted(providers.items(), key=lambda pair: pair[1])
        catalog = self.plugin_manager.widget_catalog() if self.plugin_manager else None
        factory = getattr(catalog, "build_query_controls", None)
        controls = None
        if callable(factory):
            try:
                controls = factory(providers, changed, activate, library=library)
            except Exception as exc:
                self.diagnostics.add("PLUGIN", "query widget", "error", detail={"error": str(exc)})
        if controls is None:
            controls = QueryControls(providers, changed, activate, library=library)
        setter = getattr(controls, "set_provider_search_capabilities", None)
        if callable(setter) and not library:
            setter(self.service.provider_search_capabilities())
        return controls

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
    ) -> bool:
        """Search from a trusted book detail; exactly one explicit query.

        This API is public through UiSearchNavigationHost. No credentials,
        selected identity or old filters are copied into a new search.
        """
        return search_controller.search_books(self, query, field=field, online=online, identity_id=identity_id)

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
        page = MasterDetailPage(self, "Historique", "Documents précédemment empruntés", "Historique non chargé", "Ouvrez cet écran ou actualisez pour récupérer l'historique.")
        page.set_total_only_count(True)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic"); refresh.set_tooltip_text("Actualiser l'historique")
        refresh.connect("clicked", lambda _b: self.refresh_history(True, refresh)); page.header_actions.append(refresh)
        from cifereca.language import language_label
        self.history_languages = MultiSelect('Langues', [(code, language_label(code)) for code in ('fr','en','de','es','it','pt','nl')], self._rerender_history)
        page.toolbar.append(self.history_languages)
        page.connect_selected(self._history_selected)
        page.scroll.get_vadjustment().connect("value-changed", self._history_scroll_changed)
        return page

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
        if not preserve_all:
            self._history_all_rows = list(rows)
        wanted_languages = set(self.history_languages.values()) if hasattr(self, 'history_languages') else set()
        if wanted_languages:
            rows = [x for x in rows if self._known_item_languages(x.source_provider, x.record_id or x.id).intersection(wanted_languages)]
        self.history_objects = {x.id: x for x in rows}
        self._history_rows = list(rows)
        self._history_loaded_count = 0
        self._history_append_scheduled = False
        initial = self._history_rows[: self._history_page_size]
        self._history_loaded_count = len(initial)
        self.history_page.show_rows(
            [self._history_book_row(x) for x in initial],
            total_count=len(self._history_rows),
        )
        if len(self._history_rows) > self._history_loaded_count:
            self.diagnostics.add(
                "UI", "history progressive load", "ok",
                detail={
                    "loaded": self._history_loaded_count,
                    "total": len(self._history_rows),
                    "page_size": self._history_page_size,
                },
                level="detailed",
            )

    def _history_scroll_changed(self, adjustment: Gtk.Adjustment) -> None:
        if getattr(self, "_closed", False):
            return
        if self._history_loaded_count >= len(self._history_rows) or self._history_append_scheduled:
            return
        remaining = adjustment.get_upper() - (adjustment.get_value() + adjustment.get_page_size())
        threshold = max(420.0, adjustment.get_page_size() * 0.75)
        if remaining <= threshold:
            self._history_append_scheduled = True
            GLib.idle_add(self._append_history_chunk)

    def _append_history_chunk(self) -> bool:
        self._history_append_scheduled = False
        # A queued GTK idle may outlive the window and its history widgets.
        if getattr(self, "_closed", False):
            return False
        if self._history_loaded_count >= len(self._history_rows):
            return False
        start = self._history_loaded_count
        end = min(len(self._history_rows), start + self._history_page_size)
        chunk = self._history_rows[start:end]
        self._history_loaded_count = end
        self.history_page.append_rows(
            [self._history_book_row(x) for x in chunk],
            total_count=len(self._history_rows),
        )
        self.diagnostics.add(
            "UI", "history progressive load", "ok",
            detail={
                "loaded": self._history_loaded_count,
                "total": len(self._history_rows),
                "page_size": self._history_page_size,
            },
            level="detailed",
        )
        return False

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
        page = MasterDetailPage(self, "Réservations", "Réservations physiques et numériques", "Aucune réservation", "Vos réservations apparaîtront ici.")
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic"); refresh.set_tooltip_text("Actualiser les réservations")
        refresh.connect("clicked", lambda _b: self.refresh_bookings(True, refresh)); page.header_actions.append(refresh)
        page.connect_selected(self._booking_selected)
        return page

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
        self.booking_objects = {x.id: x for x in rows}
        ui_rows = []
        for item in rows:
            presentation = booking_presentation(item)
            ui_rows.append(BookListItem(
                item.id,
                item.title,
                item.location or "",
                f"Réservé le {fr_date(item.booking_date)}",
                presentation.status_label,
                None,
            ))
        objects={x.id:{"source_provider":x.source_provider} for x in rows}
        self._attach_row_facets(ui_rows,objects,self.search_controls)
        self.booking_page.show_rows(ui_rows)

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
            return "Possédé sur Kobo · en ligne" if item.get("provider_id") == "kobo" else "Possédé en ligne"
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
            return "Distant"
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
        if getattr(self, "_acsm_chooser", None):
            self._acsm_chooser.present(); return
        identity = self.service.current_identity
        if identity is None:
            self.show_error("Choisissez une identité pour importer un ACSM"); return
        identity_id = identity.identity_id
        chooser = Gtk.FileChooserDialog(title=f"Importer un ACSM — {identity.label}", transient_for=self, modal=True, action=Gtk.FileChooserAction.OPEN)
        chooser.add_buttons("Annuler", Gtk.ResponseType.CANCEL, "Importer", Gtk.ResponseType.ACCEPT)
        self._acsm_chooser = chooser
        filt = Gtk.FileFilter(); filt.set_name("ACSM (*.acsm)"); filt.add_pattern("*.acsm"); filt.add_pattern("*.ACSM"); chooser.add_filter(filt)
        def chosen(dialog, response):
            file = dialog.get_file() if response == Gtk.ResponseType.ACCEPT else None
            path = file.get_path() if file else None
            dialog.destroy()
            self._acsm_chooser = None
            if not path: return
            def done(result):
                if result.get("status") == "attention_required":
                    self.show_attention("Import en attente : " + str(result.get("code") or "runtime DRM à configurer") + "\nLe staging est conservé pour une nouvelle tentative.", code=result.get("code") or "attention_required")
                else:
                    self._activity_result(
                        "ACSM importé" if result.get("status") != "unchanged" else "ACSM déjà importé"
                    )
                    self.refresh_library(False)
            self.runner.submit("Import ACSM…", lambda: self.service.import_acsm(Path(path), identity_id=identity_id), done,
                priority=TaskPriority.USER_ACTION, kind="acsm-import")
        chooser.connect("response", chosen); chooser.present()

    def _search_facet(self, field, text, controls):
        from .query_model import apply_facet
        state = controls.state()
        updated = apply_facet(state, field, text)
        # Detail facets are an explicit action: update controls silently, then
        # refresh the owning context exactly once. This also re-runs the action
        # when the selected facet already matches the current filter.
        library_controls = getattr(self, 'library_controls', None)
        target_controls = library_controls if controls is library_controls else self.search_controls
        target_controls.set_state(updated, notify=False)
        if target_controls is library_controls:
            self.navigate('library')
            self._render_library()
            return
        self.navigate("search")
        self.run_search(False)

    def _facet_values(self, item):
        from .book_facets import facets_for_book
        return [(f.field,f.label,f.value) for f in facets_for_book(item,self.service.provider_display_name)]


    def _facet_value_button(self, label, field, value, controls):
        caption = str(label or value or "").strip()
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("facet-value")
        text = Gtk.Label(label=caption, xalign=0)
        text.set_ellipsize(Pango.EllipsizeMode.END)
        text.set_max_width_chars(28)
        button.set_child(text)
        tooltip = f"Filtrer par {caption}"
        button.set_tooltip_text(tooltip)
        try:
            button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        except (AttributeError, TypeError):
            pass
        button._cifereca_facet_field = str(field)
        button._cifereca_facet_value = value
        button.connect(
            "clicked",
            lambda _button, f=field, v=value: self._search_facet(f, v, controls),
        )
        return button


    def _facet_value_group(self, choices, field, controls):
        group = Gtk.FlowBox()
        group.set_selection_mode(Gtk.SelectionMode.NONE)
        group.set_halign(Gtk.Align.START)
        group.set_valign(Gtk.Align.CENTER)
        group.set_min_children_per_line(1)
        group.set_max_children_per_line(4)
        group.set_column_spacing(4)
        group.set_row_spacing(4)
        seen = set()
        for label, value in choices:
            key = (str(label or "").strip().casefold(), str(value or ""))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            group.insert(self._facet_value_button(label, field, value, controls), -1)
        return group

    def _attach_row_facets(self, rows, objects, controls):
        from .book_facets import facets_for_book, project_list_facets
        allowed = {'author': 0, 'format': 1, 'language': 2, 'collection': 3, 'source': 4}
        for row in rows:
            facets=[
                facet for facet in facets_for_book(
                    objects.get(row.key,{}),
                    self.service.provider_display_name,
                )
                if facet.field in allowed
            ]
            facets.sort(key=lambda facet:(allowed[facet.field], facet.label.casefold()))
            facets=project_list_facets(facets)
            row.tags=[
                (
                    facet,
                    None if facet.field=='_overflow' else
                    (lambda _b,f=facet.field,text=facet.value:self._search_facet(f,text,controls)),
                )
                for facet in facets
            ]
            row.author_actions=[];row.source_action=None;row.facet_actions=[]


    def _add_facets(self, detail, item, controls):
        values = self._facet_values(item)
        value_map = {}
        for field, label, value in values:
            value_map.setdefault(field, []).append((label, value))

        row_fields = {
            "Source": "source",
            "Provenance": "source",
            "Fournisseur": "source",
            "Format": "format",
            "Support": "support",
            "Auteur": "author",
            "Éditeur": "publisher",
            "Sujets": "subject",
            "Sujet": "subject",
            "Collection / série": "collection",
            "Collection": "collection",
            "Série": "series",
            "ISBN": "isbn",
            "Identifiants": "isbn",
            "Langue": "language",
            "Langues": "language",
        }
        subtitle_fallback_fields = {
            "author", "publisher", "subject", "series", "collection", "language",
        }

        def bind_rows(widget):
            # A native GTK type/title guard survives GObject wrapper recreation:
            # each Détail row retains its own subtitle and Copier/Rechercher.
            if isinstance(widget, getattr(Adw, "ExpanderRow", ())) and widget.get_title() == "Détail":
                return
            if isinstance(widget, Adw.ActionRow):
                field = row_fields.get(widget.get_title())
                original_subtitle = getattr(
                    widget,
                    "_cifereca_facet_original_subtitle",
                    widget.get_subtitle(),
                )
                widget._cifereca_facet_original_subtitle = original_subtitle

                previous = getattr(widget, "_cifereca_facet_values", None)
                if previous is not None:
                    try:
                        widget.remove(previous)
                    except (AttributeError, TypeError):
                        previous.set_visible(False)
                    widget._cifereca_facet_values = None

                choices = list(value_map.get(field, ())) if field else []
                if not choices and field in subtitle_fallback_fields:
                    displayed = str(original_subtitle or "").strip()
                    if displayed and displayed != "—":
                        choices = [(displayed, displayed)]

                widget.set_activatable(False)
                if choices:
                    widget.set_subtitle("")
                    facet_values = self._facet_value_group(choices, field, controls)
                    facet_values.set_tooltip_text(
                        "Valeurs filtrables : utilisez Tab puis Entrée ou Espace"
                    )
                    widget.add_suffix(facet_values)
                    widget._cifereca_facet_values = facet_values
                else:
                    widget.set_subtitle(original_subtitle)

            child = widget.get_first_child()
            while child:
                bind_rows(child)
                child = child.get_next_sibling()

        bind_rows(detail.content)

        subtitle = getattr(detail, "_subtitle_label", None)
        authors = list(value_map.get("author", ()))
        if subtitle is not None:
            author_box = getattr(subtitle, "_cifereca_author_facets", None)
            if author_box is not None:
                parent = author_box.get_parent()
                if parent is not None:
                    parent.remove(author_box)
                subtitle._cifereca_author_facets = None
            subtitle.set_visible(True)
            if authors:
                parent = subtitle.get_parent()
                if parent is not None:
                    subtitle.set_visible(False)
                    author_box = self._facet_value_group(authors, "author", controls)
                    author_box.add_css_class("detail-subtitle")
                    parent.append(author_box)
                    subtitle._cifereca_author_facets = author_box

        identity = self.service.current_identity
        binding = self.service.adept_return_for_artifact(
            str(item.get("artifact_id") or ""),
            identity.identity_id if identity else "",
        )
        if binding:
            button = Gtk.Button(label="Restituer le prêt ADEPT")
            button.set_tooltip_text(
                "Restituer avec le profil d’origine ; conserver le fichier local. "
                "Le retour fournisseur reste une action distincte."
            )
            def return_loan(_button):
                self.runner.submit(
                    "Restitution du prêt ADEPT…",
                    lambda: self.service.return_adept_acquisition(binding["acquisition_id"]),
                    lambda _result: (
                        button.set_sensitive(False),
                        self._activity_result(
                            "Retour enregistré, jeton absent · fichier conservé"
                            + (
                                " · état local vérifié"
                                if _result.get("verification") == "confirmed"
                                else " · vérification locale non confirmée"
                            )
                        ),
                    ),
                    lambda exc: self.show_error(str(exc), "Restitution ADEPT impossible"),
                    control=button,
                    priority=TaskPriority.USER_ACTION,
                )
            button.connect("clicked", return_loan)
            detail.add_actions([button])

    def _decorate_book_detail(self, detail, item):
        library_detail = getattr(getattr(self, 'library_page', None), 'detail', None)
        controls = self.library_controls if detail is library_detail else self.search_controls
        self._add_facets(detail, item, controls)
        self._add_local_book_actions(detail, item)
        self._add_plugin_book_actions(detail, item)

    def _book_action_context(self, item) -> UiActionContext:
        metadata = dict(item.get("metadata") or {}) if isinstance(item, dict) else {}
        book = book_ref_from_row(item)
        artifact = None
        path_value = item.get("local_path") or metadata.get("path")
        artifact_id = str(item.get("artifact_id") or "")
        if path_value and artifact_id:
            path = Path(str(path_value))
            size = 0
            try: size = path.stat().st_size
            except OSError: pass
            sha = ""
            try:
                row = next((r for r in self.service.storage.artifacts() if str(r.get("artifact_id") or "") == artifact_id), None)
                if row: sha = str(row.get("sha256") or "")
            except Exception: pass
            fmt = str(item.get("media_format") or path.suffix.lstrip(".") or "")
            artifact = ArtifactRef(artifact_id=artifact_id, book_id=book.book_id or None,
                format=ArtifactFormat("application/epub+zip" if fmt.lower() == "epub" else "application/octet-stream", fmt),
                size_bytes=size, sha256=sha, filename=path.name)
        return UiActionContext(target="book.detail", route=self.view_stack.get_visible_child_name() or "", book=book, artifact=artifact, metadata=metadata)

    def _add_plugin_book_actions(self, detail, item) -> None:
        pages = getattr(self, "_pages", {}).values()
        context = self._book_action_context(item)
        buttons = []
        for _route, action, enabled in contextual_actions(pages, "book.detail", context):
            button = self.action_button(action.title, action.icon_name)
            button._cifereca_action_id = "plugin:" + action.id
            button.set_sensitive(enabled)
            button.connect("clicked", lambda _b, a=action, c=context: self._invoke_ui_action(a, self._UiHost(self, c)))
            buttons.append(button)
        if buttons:
            detail.add_actions(buttons)

    def _known_search_result(self, row):
        meta = row.get("metadata") or {}
        raw = meta.get("search_result")
        if isinstance(raw, dict):
            try: return SearchResult(**raw)
            except TypeError: pass
        provider = row.get("provider_id")
        if provider == "local": return None
        return SearchResult(str(row.get("key")), str(row.get("item_id") or ""), str(meta.get("docbase") or ""),
            "digital", str(row.get("title") or ""), row.get("author"), None, row.get("media_format"),
            meta.get("publisher"), None, None, row.get("thumbnail_url"), None, str(meta.get("scenario") or ""),
            source_provider=str(provider), metadata=meta)

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

        Public plugin callers cannot navigate to another identity implicitly.
        The lookup uses only the already loaded Library snapshot, with no
        filesystem scan or network operation on the GTK main thread.
        """
        if getattr(self, "_closed", False):
            return False
        from ..core.book_navigation import library_key_for_book

        identity = getattr(self.service, "current_identity", None)
        active_id = str(getattr(identity, "identity_id", "") or "")
        if not active_id or (identity_id is not None and identity_id != active_id):
            return False
        key = library_key_for_book(
            book, getattr(self, "_library_entries_cache", ()), identity_id=active_id,
        )
        if not key:
            return False
        self._open_library_item(key)
        return True

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
        placement_id=str(item.get('placement_id') or '')
        identity_id=str(item.get('identity_id') or '')
        if not placement_id:
            provider=item.get('provider_id') or item.get('source_provider')
            credential=item.get('source_credential') or (item.get('metadata') or {}).get('source_credential')
            item_id=item.get('item_id') or item.get('record_id')
            current=getattr(self.service,'current_identity',None)
            if not provider:
                if item.get('title'): self._add_catalogue_action(detail, item)
                return
            if not credential or not item_id or not current:
                self._add_catalogue_action(detail, item); return
            placement=self.service.local_placement_for_source(provider_id=provider,credential_id=credential,
                item_id=item_id,identity_id=current.identity_id)
            if not placement:
                self._add_catalogue_action(detail, item); return
            placement_id=placement['placement_id'];identity_id=current.identity_id
        if not identity_id:
            self._add_catalogue_action(detail, item); return
        try:self.service.local_book_path(placement_id,identity_id=identity_id)
        except Exception:
            self._add_catalogue_action(detail, item); return
        opening=self.action_button('Ouvrir','document-open-symbolic')
        opening._cifereca_action_id = 'open-local'
        def open_book(_button):
            try:
                # Recheck existence and identity at activation, then launch without a shell.
                uri=self.service.local_book_path(placement_id,identity_id=identity_id).as_uri()
                Gio.AppInfo.launch_default_for_uri(uri,None)
            except Exception as exc:self.show_error(str(exc),'Ouverture du livre impossible')
        opening.connect('clicked',open_book)
        buttons=[opening]
        source_provider = str(item.get('provider_id') or item.get('source_provider') or 'local')
        source_credential = str(item.get('source_credential') or (item.get('metadata') or {}).get('source_credential') or '')
        source_item_id = str(item.get('item_id') or item.get('source_item_id') or (item.get('record_id') or ''))
        known_possessions = ()
        manager = getattr(self, 'plugin_manager', None)
        library_read = manager.broker('library.read') if manager else None
        if library_read is not None:
            try:
                logical = library_read.book(source_item_id) if source_item_id else None
                if logical is None and item.get('artifact_id'):
                    logical = library_read.for_artifact(str(item.get('artifact_id')))
                known_possessions = tuple(logical.possessions) if logical is not None else ()
            except Exception as exc:
                self.diagnostics.add(
                    'UI', 'read known possessions', 'error',
                    detail={'error': type(exc).__name__, 'message': str(exc)},
                )
        for target in self.service.identities:
            if target.identity_id==identity_id:continue
            # A current Core possession is authoritative even when there is no
            # local file yet for the target identity.
            if source_item_id and identity_has_attested_possession(
                known_possessions,
                identity_id=target.identity_id,
                provider_id=source_provider,
                source_item_id=source_item_id,
                credential_id=source_credential,
            ):
                continue
            # Keep the placement fallback for older/local-only records that are
            # not represented in the logical possession projection.
            if source_item_id:
                try:
                    already_owned = self.service.local_placement_for_source(
                        provider_id=source_provider, credential_id=source_credential,
                        item_id=source_item_id, identity_id=target.identity_id,
                    ) is not None
                except Exception:
                    already_owned = False
                if already_owned:
                    continue
            button=self.action_button('Envoyer à '+target.label,'document-send-symbolic')
            button._cifereca_action_id = 'send-local:'+target.identity_id
            def send(_button,target_id=target.identity_id):
                def work():
                    self.service.local_book_path(placement_id,identity_id=identity_id)
                    return self.service.materialize_to_identity(placement_id,target_id)
                self.runner.submit('Envoi du fichier local…',work,
                    lambda _result:(self._activity_result('Livre envoyé'),self.refresh_recent(),self.refresh_library(False)),
                    control=_button,priority=TaskPriority.USER_ACTION,kind='materialize')
            button.connect('clicked',send);buttons.append(button)
        routing = manager.broker('artifact.routing') if manager else None
        context = self._book_action_context(item) if routing is not None else None
        if routing is not None and context is not None and context.artifact is not None:
            try:
                route_request = ArtifactRouteRequest(context.artifact, identity_id=identity_id or None)
                route_targets = tuple(routing.targets(route_request))
            except Exception as exc:
                route_targets = ()
                self.diagnostics.add('UI','artifact routing targets','error',detail={'error':type(exc).__name__,'message':str(exc)})
            if route_targets:
                menu = Gtk.MenuButton(label='Envoyer vers…')
                menu._cifereca_action_id = 'artifact-routing'
                menu.set_tooltip_text('Envoyer cet artefact vers une cible de stockage compatible')
                popover = Gtk.Popover()
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                box.set_margin_top(8); box.set_margin_bottom(8); box.set_margin_start(8); box.set_margin_end(8)
                for target in route_targets:
                    target_button = Gtk.Button(label=str(target.target.label or target.target.target_id))
                    target_button.add_css_class('flat')
                    def route(_button, target_id=target.target.target_id):
                        request = ArtifactRouteRequest(context.artifact, target_id=target_id, identity_id=identity_id or None)
                        def work():
                            plan = routing.plan(request)
                            return routing.transfer(plan)
                        self.runner.submit('Envoi vers le stockage…', work,
                            lambda _result:(self._activity_result('Livre envoyé'), self.refresh_library(False), self.refresh_recent()),
                            lambda exc:self.show_error(str(exc),'Envoi impossible'), control=_button,
                            priority=TaskPriority.USER_ACTION, kind='artifact-route')
                    target_button.connect('clicked', route); box.append(target_button)
                popover.set_child(box); menu.set_popover(popover); buttons.append(menu)
        detail.add_actions(buttons)

    def _open_placement(self, placement):
        try:
            current = self.service.current_identity
            if not current or current.identity_id != placement['identity_id']:
                raise ValueError('L’identité active a changé')
            uri = self.service.local_book_path(placement['placement_id'], identity_id=current.identity_id).as_uri()
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception as exc: self.show_error(str(exc), 'Ouverture du livre impossible')

    def _add_catalogue_action(self, detail, item):
        target = self.service.public_catalogue_target(item)
        if not target:
            return
        kind, url = target
        if url in getattr(detail, "_rendered_document_urls", set()):
            return
        is_notice = kind == 'notice'
        button = self.action_button(
            'Ouvrir la notice' if is_notice else 'Ouvrir le catalogue',
            'web-browser-symbolic',
        )
        button._cifereca_action_id = 'open-notice' if is_notice else 'open-catalogue'
        button.set_tooltip_text(
            'Ouvrir la page publique de ce livre'
            if is_notice else 'Ouvrir le catalogue général pour y rechercher ce livre'
        )

        def open_catalogue(_button):
            try:
                # The classification may have changed since rendering. Never
                # turn a button promising a book notice into a generic homepage.
                current = self.service.public_catalogue_target(item)
                if current and current[0] == kind:
                    Gio.AppInfo.launch_default_for_uri(current[1], None)
                elif current:
                    self.toast('La destination du lien a changé ; actualisez la fiche.')
            except Exception as exc:
                self.show_error(str(exc), 'Ouverture de la page publique impossible')

        button.connect('clicked', open_catalogue)
        detail.add_actions([button])


    @staticmethod
    def _book_description(item):
        metadata = item.get('metadata') or {}
        return (metadata.get('epub_metadata') or {}).get('description') or metadata.get('description')

    def _add_book_notice(self, detail, item, *, include_description=True):
        metadata=item.get('metadata') or {}
        if include_description:
            detail._show_description(self._book_description(item))
        essential_labels={'Source','Provenance','Format','Langue','Langues','Disponibilité'}
        rows=[row for row in notice_rows(metadata) if row[0] not in essential_labels]
        if rows:detail.add_group('Notice EPUB' if metadata.get('epub_metadata') else 'Notice',rows)
        catalogue=metadata.get('catalogue_metadata') or {}
        if catalogue:
            catalogue_rows=[row for row in notice_rows(catalogue) if row[0] not in essential_labels]
            if catalogue_rows:detail.add_group('Catalogue',catalogue_rows)
        if metadata.get('epub_metadata_schema'):
            cover=metadata.get('epub_cover') or {}
            labels={'extracted':'Extraite du fichier','no_usable_image':'Aucune image exploitable','metadata_unreadable':'Métadonnées illisibles'}
            status=metadata.get('epub_cover_status') or 'not_inspected'
            facts=[('Couverture',labels.get(status,'À réinspecter'))]
            if cover.get('method'):facts.append(('Détection',{'declared':'Déclaration EPUB','declared_page':'Page de couverture déclarée','guide':'Guide de couverture','first_spine':'Image unique de la première page (déduction)'}.get(cover['method'],cover['method'])))
            detail.add_group('Image du livre',facts)

    def _recent_selected(self, row: BookListItem | None) -> None:
        if not row:
            return
        item = self._recent_objects.get(row.key)
        if not item:
            return
        detail = self.recent_page.detail
        provider = str(item.get("provider_id") or "local")
        full_path = str(item.get("path") or "")
        file_name = Path(full_path).name if full_path else "—"
        acquired = format_recent_timestamp(item.get("acquired_at"), detailed=True)
        detail.show_summary(
            title=str(item.get("title") or "Sans titre"),
            subtitle=str(item.get("author") or ""),
            thumbnail=item.get("thumbnail_url"),
            badges=[provider.capitalize(), "Local"],
            group_title="Récupération",
            rows=[
                ("Fichier", file_name),
                ("Récupéré", acquired),
                ("Provenance", provider.capitalize()),
                ("Chemin", full_path or "—"),
            ],
        )
        self._add_book_notice(detail,item)
        self._add_local_book_actions(detail,item)
        self._add_facets(detail, item, self.search_controls)

    # Compatibility name used by the 0.8.0 refresh paths.
    def _render_kobo_loans(self) -> None:
        self._render_library()


    def _book_recommendation_target(self, item):
        manager = getattr(self, "plugin_manager", None)
        if manager is None:
            return None
        context = self._book_action_context(item)
        book = context.book
        if book is None:
            return None
        try:
            services = manager.services("recommendation")
        except Exception:
            return None
        for resolved in services:
            if "recommendation.book-reference" not in set(resolved.capabilities):
                continue
            service = resolved.service
            if isinstance(service, BookReferenceRecommendationService):
                return service, book
        return None

    def _add_book_recommendation_action(self, detail, item) -> None:
        if self._book_recommendation_target(item) is None:
            return
        button = self.action_button(
            "Recommander à partir de ce livre",
            "starred-symbolic",
        )
        button._cifereca_action_id = "recommend-from-book"
        button._cifereca_action_priority = 25

        def recommend(_button):
            target = self._book_recommendation_target(item)
            if target is None:
                self.toast("Le service de recommandation n’est plus disponible.")
                return
            service, book = target
            try:
                route = str(service.recommend_from_book(book) or "").strip()
            except Exception as exc:
                self.show_error(str(exc), "Recommandation impossible")
                return
            if not route:
                self.show_error(
                    "Le service n’a fourni aucune page à ouvrir.",
                    "Recommandation impossible",
                )
                return
            self.navigate(route)

        button.connect("clicked", recommend)
        detail.add_actions([button])

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
        options = result["options"]
        if not options:
            self.toast("Aucun prêt ADEPT dans les profils liés" if result["complete"] else "Inventaire incomplet : aucun prêt disponible", 6)
            return
        dialog = Adw.MessageDialog.new(self, "Rattacher un prêt ADEPT", "Choisissez le prêt correspondant au fichier local. Le titre aide au choix ; il ne constitue pas une preuve automatique. Aucun livre ne sera téléchargé ni restitué.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        title = Gtk.Label(label=loan.title, xalign=0); title.set_wrap(True); box.append(title)
        if not result["complete"]:
            warning = Gtk.Label(label="Inventaire partiel : seuls les profils lus avec succès sont proposés.", xalign=0); warning.set_wrap(True); box.append(warning)
        labels = [f"{o['title']} · {o['profile_name']} · {o['state']} · {o['loan_id']}" for o in options]
        dropdown = Gtk.DropDown.new_from_strings(labels); dropdown.set_enable_search(True)
        dropdown.set_selected(Gtk.INVALID_LIST_POSITION); dropdown.set_tooltip_text("Titre · profil · état · identifiant ADEPT")
        box.append(dropdown)
        detail = Gtk.Label(label="Sélectionnez un prêt pour voir sa validité et son chemin connu.", xalign=0); detail.set_wrap(True); box.append(detail)
        dialog.set_extra_child(box); dialog.add_response("cancel", "Annuler"); dialog.add_response("attach", "Confirmer le rattachement")
        dialog.set_response_enabled("attach", False); dialog.set_close_response("cancel")
        def selected(_dropdown, _pspec):
            index = dropdown.get_selected(); valid = index < len(options)
            dialog.set_response_enabled("attach", valid)
            if valid:
                option = options[index]; detail.set_label(f"Validité : {option.get('validity') or 'Inconnue'}\nChemin ADEPT : {option.get('local_book') or 'Non fourni'}")
        dropdown.connect("notify::selected", selected)
        def response(_dialog, choice):
            index = dropdown.get_selected()
            if choice != "attach" or index >= len(options): return
            option = options[index]
            def ok(_result):
                self.loan_page.detail.show_loan(loan); self.refresh_library(False)
                self._activity_result("Prêt ADEPT rattaché · fichier conservé")
            self.runner.submit("Rattachement du prêt ADEPT…", lambda: self.service.attach_adept_loan(acquisition_id, option["profile_id"], option["loan_id"]), ok,
                lambda exc: self.show_error(str(exc), "Rattachement impossible"), priority=TaskPriority.USER_ACTION, kind="adept-binding")
        dialog.connect("response", response); dialog.present()

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

    def prepare_pnb_for_loan(self, loan: Loan, send: bool = False, control: Gtk.Widget | None = None) -> None:
        def work():
            if send:
                raise RuntimeError("L’envoi vers une liseuse nécessite un plugin storage.target.")
            return self.service.reacquire_digital_loan(loan)
        def ok(result):
            self.refresh_library(False); self.refresh_recent(); self._render_dashboard()
            self.loan_page.detail.show_loan(loan)
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
            session = self.service._session_for_source(item.source_provider, item.source_credential)
            return bool(session.connected and session.capabilities.borrow)
        except Exception:
            return False

    def _eligible_borrow_accounts(self, item: SearchResult):
        session = self.service._session_for_source(item.source_provider, item.source_credential)
        if not session.capabilities.borrow:
            return session, []
        identity_id = self.service.current_identity.identity_id if self.service.current_identity else None
        accounts = [account for account in session.accounts if account.is_primary and (not identity_id or account.identity_id == identity_id)]
        if not accounts:
            accounts = [account for account in session.accounts if account.is_primary]
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
        account = account or accounts[0]

        def work():
            loan = session.borrow(item, account)
            loan.identity_id = loan.identity_id or account.identity_id
            if loan.source_account_id is None:
                loan.source_account_id = account.unique_id or "primary"
            loans = self.service.refresh_identity_loans(force=True, identity_id=account.identity_id)
            return loan, loans

        def ok(value) -> None:
            loan, _loans = value
            self._activity_result(f"Emprunt confirmé : {loan.title}")
            self.refresh_library(False)
            self.refresh_recent()

        self.runner.submit(f"Emprunt depuis {item.source_provider}…", work, ok, control=control,
            priority=TaskPriority.USER_ACTION, kind="borrow")

    def download_direct_item(self, item: SearchResult, control: Gtk.Widget | None = None) -> None:
        def work():
            return self.service.obtain_search_item(item)

        def ok(result) -> None:
            acquisition = result.acquisition
            if result.path is not None:
                self._activity_result(f"Disponible dans la Bibliothèque : {result.path.name}")
                self.refresh_library(False)
                self.refresh_recent()
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
        page_lifetime = SettingsPageLifetime()
        lifetime_name = "_settings_standalone_lifetime" if standalone else "_settings_main_lifetime"
        previous = getattr(self, lifetime_name, None)
        if previous is not None:
            previous.dispose()
        setattr(self, lifetime_name, page_lifetime)
        shell = build_settings_shell(Gtk=Gtk, Adw=Adw, add_css=add_css)
        scroll = shell.scroll
        box = shell.root
        settings_root = shell.root
        settings_heading = shell.heading
        tabs = shell.tabs
        sections = shell.sections
        if not standalone:
            self._settings_tabs = tabs

        settings_state = SettingsRevisionState(sections)
        (
            update_settings_actions, mark_settings_dirty,
            track_all_settings_widgets, settings_action_widgets,
        ) = bind_settings_actions(
            tabs=tabs, sections=sections, settings_state=settings_state,
            page_lifetime=page_lifetime, Gtk=Gtk, Adw=Adw,
        )

        box = sections["accounts"]

        descriptors = {d.id: d for d in self.service.provider_descriptors()}
        public_ids = {provider_id for provider_id in descriptors
                      if getattr(getattr(self.service._provider_plugin_status(provider_id), "descriptor", None), "instantiation", "multiple") == "singleton_public"}

        identities_container = build_identity_settings_header(
            section=box, Gtk=Gtk, add_css=add_css,
        )
        identity_editors: list[dict[str, Any]] = []
        configured_identities = self.config.identities()

        def add_identity_editor(data: dict[str, Any] | None = None) -> None:
            data = dict(data or {})
            existing_ids = {str(editor["id"].get_text()).strip() for editor in identity_editors}
            identity_id = str(data.get("id") or "identite").strip() or "identite"
            base = identity_id
            index = 2
            while identity_id in existing_ids:
                identity_id = f"{base}-{index}"; index += 1
            # Only the widget tree is extracted; MainWindow keeps bindings,
            # chooser callbacks, dirty revisions and the async save contract.
            adept_profile = str(
                data.get("adept_profile")
                or self.service.storage.adept_profile_binding_get("identity", identity_id)
                or ""
            )
            normal_subtitle = (
                normal_paris_adept_summary(
                    self.service.normal_paris_adept_status(identity_id)
                )
                if data.get("id")
                else "À configurer · enregistrez l’identité pour vérifier ses liaisons."
            )
            editor, choose_directory, choose_adept, delete_button = build_identity_settings_editor(
                data=data, identity_id=identity_id,
                adept_profile=adept_profile, normal_subtitle=normal_subtitle,
                identities_container=identities_container, Gtk=Gtk, Adw=Adw,
            )
            identity_editors.append(editor)
            directory_row = editor["directory"]
            adept_row = editor["adept_profile"]
            choose_directory.connect(
                "clicked",
                lambda _b, entry=directory_row: self._choose_identity_directory(
                    entry, Path(library_dir_row.get_text().strip() or "~/EBooks")
                ),
            )
            choose_adept.connect(
                "clicked", lambda button, entry=adept_row:
                self._choose_adept_profile(entry, button),
            )
            def delete_identity(_button, e=editor):
                e["deleted"] = True
                e["group"].set_visible(False)
                mark_settings_dirty("accounts")
            delete_button.connect("clicked", delete_identity)

        for configured_identity in configured_identities:
            add_identity_editor(configured_identity)
        add_identity = Gtk.Button(label="Ajouter une identité")
        def add_identity_clicked(_button):
            add_identity_editor({})
            mark_settings_dirty("accounts")
        add_identity.connect("clicked", add_identity_clicked); box.append(add_identity)

        account_section = build_provider_accounts_header(
            section=box, Gtk=Gtk, add_css=add_css,
        )
        account_section.plugins_button.connect("clicked", lambda _b: self.navigate("plugins"))
        accounts_container = account_section.accounts_container
        account_editors: list[dict[str, Any]] = []
        configured_rows = self.config.provider_accounts()

        def add_account_editor(data: dict[str, Any] | None = None, provider_id: str = "paris") -> None:
            data = dict(data or {})
            provider_id = str(data.get("provider") or provider_id).lower()
            descriptor = descriptors.get(provider_id)
            if descriptor is None or descriptor.state != "ready" or provider_id in public_ids:
                return
            existing_ids = {str(x.get("id")) for x in account_editors}
            if not data.get("id"):
                existing_ids |= {str(x.get("id")) for x in configured_rows}
            base_id = f"{provider_id}-default"
            account_id = str(data.get("id") or base_id)
            n = 2
            while account_id in existing_ids:
                account_id = f"{provider_id}-{n}"; n += 1

            editor, delete_button = build_provider_account_editor(
                data=data, provider_id=provider_id, account_id=account_id,
                descriptor=descriptor, configured_identities=configured_identities,
                accounts_container=accounts_container, config=self.config,
                Gtk=Gtk, Adw=Adw,
            )
            account_editors.append(editor)
            def delete_account(_button, e=editor):
                e["deleted"] = True
                e["group"].set_visible(False)
                mark_settings_dirty("accounts")
            delete_button.connect("clicked", delete_account)

        for configured in configured_rows:
            add_account_editor(configured, str(configured.get("provider") or "paris"))

        for add, provider_id in build_provider_account_buttons(
            section=box, descriptors=descriptors.values(),
            public_ids=public_ids, Gtk=Gtk,
        ):
            def add_account_clicked(_button, pid=provider_id):
                add_account_editor({}, pid)
                mark_settings_dirty("accounts")
            add.connect("clicked", add_account_clicked)

        # ADE is bound only at Paris Identity scope. Provider sub-accounts
        # intentionally have no ADE binding UI.

        # Source download policies remain independent of account credentials.
        download_settings = build_library_download_section(
            section=sections["library"], config=self.config,
            descriptors=descriptors, Gtk=Gtk, Adw=Adw, add_css=add_css,
        )
        download_group = download_settings.download_group
        global_dir_row = download_settings.global_dir_row
        global_ebook_row = download_settings.global_ebook_row
        global_audio_row = download_settings.global_audio_row
        global_cmd_row = download_settings.global_cmd_row
        global_post_row = download_settings.global_post_row
        provider_download_editors = download_settings.provider_download_editors

        application_ui = build_settings_application_interface(
            diagnostic_section=sections["diagnostic"],
            interface_section=sections["interface"],
            config=self.config, Gtk=Gtk, Adw=Adw,
        )
        diag_switch = application_ui.diagnostics_switch
        exp_switch = application_ui.experimental_switch
        level_drop = application_ui.level_dropdown
        levels = application_ui.levels
        application_ui.plugins_button.connect(
            "clicked", lambda _b: self.navigate("plugins"),
        )

        kobo_settings = build_kobo_sync_section(
            section=sections["sync"], download_group=download_group,
            config=self.config, Gtk=Gtk, Adw=Adw,
        )
        backup_switch = kobo_settings.backup_switch
        unlinked_switch = kobo_settings.unlinked_switch
        library_dir_row = kobo_settings.library_dir_row
        backup_dir_row = kobo_settings.backup_dir_row
        backup_interval_row = kobo_settings.backup_interval_row
        vault_profiles = kobo_settings.vault_profiles
        vault_profile_drop = kobo_settings.vault_profile_drop
        kobo_run_now = kobo_settings.run_backup_now
        kobo_status = kobo_settings.backup_status

        def refresh_kobo_backup_status(*_args) -> None:
            if not page_lifetime.alive or getattr(self, "_closed", False):
                return
            status, ready = kobo_backup_action_state(
                persisted_enabled=(self.config.get("kobo_backup.enabled", False) is True),
                draft_enabled=bool(backup_switch.get_active()),
                dirty=settings_state.dirty("sync"),
            )
            kobo_status.set_subtitle(status)
            kobo_run_now.set_sensitive(ready)

        def manual_kobo_backup(_button) -> None:
            if not page_lifetime.alive or getattr(self, "_closed", False):
                return
            # Do not initiate download from an unsaved draft. A deliberate
            # physical click is the *only* new path to Kobo acquisition.
            _, ready = kobo_backup_action_state(
                persisted_enabled=(self.config.get("kobo_backup.enabled", False) is True),
                draft_enabled=bool(backup_switch.get_active()),
                dirty=settings_state.dirty("sync"),
            )
            if ready:
                self.run_kobo_backup(manual=True, control=_button)

        kobo_run_now.connect("clicked", manual_kobo_backup)

        from .action_widgets import mirror_sensitivity
        settings_bars = build_settings_action_bars(
            root=settings_root, heading=settings_heading,
            adaptive_action_bar=AdaptiveActionBar,
            action_button=action_button, mirror_sensitivity=mirror_sensitivity,
            Gtk=Gtk,
        )
        save, reconnect = settings_bars.save, settings_bars.reconnect
        save_bottom, reconnect_bottom = settings_bars.save_bottom, settings_bars.reconnect_bottom
        settings_action_widgets.update({
            "save": (save, save_bottom),
            "reconnect": (reconnect, reconnect_bottom),
            "containers": (settings_bars.heading_bar, settings_bars.footer_bar),
        })
        # Independent, opt-in Core LLM settings. No backend contact on entry.
        ai_box = sections["ai"]
        ai_cfg = self.config.get("llm", {})
        if not isinstance(ai_cfg, dict):
            ai_cfg = {}
        ai_ui = build_ai_settings_section(
            section=ai_box, settings=ai_cfg,
            Gtk=Gtk, Adw=Adw, add_css=add_css,
        )
        ai_status = ai_ui.status
        capture_ai_settings = bind_settings_ai_callbacks(
            self, ai_ui=ai_ui, ai_cfg=ai_cfg, page_lifetime=page_lifetime,
            settings_state=settings_state,
            update_settings_actions=update_settings_actions,
            Adw=Adw, TaskPriority=TaskPriority,
        )

        tabs.connect("notify::visible-child-name", update_settings_actions)
        track_all_settings_widgets()
        # Subscribe AFTER dirty tracking so the message never claims that an
        # unsaved switch is already active. No provider access from callbacks.
        for widget, signal in (
            (backup_switch, "notify::active"),
            (unlinked_switch, "notify::active"),
            (backup_dir_row, "notify::text"),
            (backup_interval_row, "notify::text"),
            (vault_profile_drop, "notify::selected"),
        ):
            widget.connect(signal, refresh_kobo_backup_status)
        refresh_kobo_backup_status()
        update_settings_actions()

        diagnostic_ui = build_settings_diagnostics_section(
            section=sections["diagnostic"], Gtk=Gtk, Adw=Adw,
            adaptive_action_bar=AdaptiveActionBar,
            action_button=self.action_button,
        )
        box = sections["diagnostic"]
        diag_summary = diagnostic_ui.summary
        diag_filter = diagnostic_ui.filter
        diag_issues_box = diagnostic_ui.issues_box
        details_toggle = diagnostic_ui.details_toggle
        diag_text = diagnostic_ui.text
        diag_scroll = diagnostic_ui.scroll
        refresh = diagnostic_ui.refresh
        copy = diagnostic_ui.copy
        export = diagnostic_ui.export
        clear = diagnostic_ui.clear

        def toggle_diagnostic_details(button):
            visible = bool(button.get_active())
            diag_scroll.set_visible(visible)
            button.set_label("Masquer les détails techniques" if visible else "Afficher les détails techniques")
        details_toggle.connect("toggled", toggle_diagnostic_details)

        def open_diagnostic_plugin(_button, plugin_id: str) -> None:
            # A button from an old Settings tree cannot navigate a rebuilt
            # or closed window after asynchronous GTK signal delivery.
            if not page_lifetime.alive or getattr(self, "_closed", False):
                return
            self.navigate("plugins")
            page = getattr(self, "plugins_page", None)
            focus = getattr(page, "focus_plugin", None)
            if callable(focus):
                focus(plugin_id)

        def refresh_text() -> None:
            if not page_lifetime.alive:
                return
            from ..system_status import system_status
            data = system_status(self.plugin_manager)
            attention = int(data.get("attention") or 0)
            diag_summary.set_label(
                f"{data.get('ready', 0)} prêt(s) · {attention} à examiner. "
                + ("Les anomalies sont affichées en premier." if attention else "Aucune anomalie bloquante détectée.")
            )
            clear_box(diag_issues_box)
            attention_only = int(diag_filter.get_selected()) == 0
            rows = diagnostic_plugin_rows(data, attention_only=attention_only)
            if not rows:
                empty = Gtk.Label(label="Aucune anomalie à examiner.", xalign=0)
                empty.add_css_class("success"); diag_issues_box.append(empty)
            for plugin in rows:
                item = Adw.ExpanderRow()
                item.set_title(f"{plugin['display_name']} · {plugin['state_label']}")
                item.set_subtitle(plugin["summary"])
                if plugin["attention"]:
                    item.add_css_class("warning")
                action = Adw.ActionRow(); action.set_title("Action")
                action.set_subtitle("Ouvrir le gestionnaire sur ce plugin")
                open_button = Gtk.Button(label="Ouvrir Plugins")
                open_button.set_valign(Gtk.Align.CENTER)
                open_button.connect("clicked", lambda b, pid=plugin["plugin_id"]: open_diagnostic_plugin(b, pid))
                action.add_suffix(open_button); item.add_row(action)
                detail = Adw.ActionRow(); detail.set_title("Détails techniques")
                detail.set_subtitle(plugin["technical"] or "Aucun détail technique")
                detail.set_subtitle_lines(0); item.add_row(detail)
                diag_issues_box.append(item)

            content = self.diagnostics.text(__version__, {
                "transport": "httpx local direct",
                "base_url": (self.config.paris_base_url() if hasattr(self.config, "paris_base_url") else self.config.get("base_url")),
                "ui": "GTK4/libadwaita",
                "cover_pipeline": self.covers.stats(),
                "history_cover_search": self.history_cover_resolver.stats(),
                "ui_performance": self.ui_perf.snapshot(),
                "providers": self.service.provider_diagnostic_state(),
            })
            diag_text.get_buffer().set_text(content)

        settings_committer = SettingsCommitController(
            self.service.save_configuration, self.runner.submit, self.runner.present_error,
        )

        def capture_settings_values():
            identities = []
            for editor in identity_editors:
                if editor.get("deleted"):
                    continue
                row = {key: editor[key].get_text() for key in
                       ("id", "label", "directory", "adept_profile", "languages")}
                row["prefer_vo"] = editor["prefer_vo"].get_active()
                identities.append(row)
            accounts = []
            for editor in account_editors:
                if editor.get("deleted"):
                    continue
                row = {key: editor[key] for key in ("id", "provider")}
                row.update({key: editor[key].get_text() for key in ("label", "identity")})
                row["enabled"] = editor["enabled"].get_active()
                row["original_options"] = settings_committer.stage(editor.get("original_options") or {})
                row["fields"] = {key: widget.get_text() for key, widget in editor["fields"].items()}
                for key in ("base_url", "opds_url", "vault_url"):
                    widget = editor.get(key)
                    row[key] = widget.get_text() if widget is not None else None
                accounts.append(row)
            values = {
                "identities": identities, "accounts": accounts,
                "provider_downloads": {
                    pid: {key: widget.get_text() for key, widget in editor.items()}
                    for pid, editor in provider_download_editors.items()
                },
                "levels": tuple(levels), "vault_profiles": tuple(vault_profiles),
                "ai": capture_ai_settings() if settings_state.dirty("ai") else {},
            }
            values["backup_directory"] = backup_dir_row.get_text()
            values["backup_interval"] = backup_interval_row.get_text()
            values["backup_enabled"] = backup_switch.get_active()
            values["diagnostics_enabled"] = diag_switch.get_active()
            values["experimental_actions"] = exp_switch.get_active()
            values["audiobook_directory"] = global_audio_row.get_text()
            values["download_command"] = global_cmd_row.get_text()
            values["download_directory"] = global_dir_row.get_text()
            values["ebook_directory"] = global_ebook_row.get_text()
            values["post_download_command"] = global_post_row.get_text()
            values["diagnostics_level_index"] = level_drop.get_selected()
            values["library_directory"] = library_dir_row.get_text()
            values["allow_unlinked_acquisition"] = unlinked_switch.get_active()
            values["vault_profile_index"] = vault_profile_drop.get_selected()
            return values

        def save_settings(_button=None, *, reconnect_after=False) -> bool:
            if not page_lifetime.alive or getattr(self, "_closed", False):
                return False
            if settings_committer.pending:
                return False
            try:
                draft = build_settings_draft(
                    self.config, capture_settings_values(), descriptors=descriptors,
                    public_ids=public_ids,
                    current_dirs={i.identity_id: i.directory_name for i in self.service.identities},
                    has_active_tasks=bool(getattr(self, "_active_tasks", {})),
                    ai_dirty=settings_state.dirty("ai"),
                )
            except SettingsValidationError as exc:
                if exc.title is None:
                    self.show_error(str(exc))
                else:
                    self.show_error(str(exc), exc.title)
                return False
            directory_change = draft.directory_change
            ai_restart_required = draft.ai_restart_required
            new_ai_settings = draft.data.get("llm", {})
            staged = settings_committer.stage(draft.data)
            staged_settings_revisions = settings_state.snapshot()
            def ok(result):
                if not page_lifetime.alive:
                    return
                self.diagnostics.enabled = bool(staged["diagnostics"]["enabled"])
                self.diagnostics.level = staged["diagnostics"]["level"]
                from ..logging_utils import set_diagnostics_level
                set_diagnostics_level(self.diagnostics.level)
                message = "Configuration enregistrée"
                if ai_restart_required:
                    # Cancel/reset must now restore the newly saved values.
                    ai_cfg.clear()
                    ai_cfg.update(new_ai_settings)
                    message = "Configuration IA enregistrée — redémarrage nécessaire"
                    ai_status.set_text(
                        "Enregistré · redémarrer Cifereca pour actualiser "
                        "le service IA partagé par les plugins"
                    )

                if result.get("migrated"):
                    message = f"Répertoire déplacé · {result['collisions']} collision(s) préservée(s) · bibliothèque actualisée"
                    self.refresh_library(False); self.refresh_recent(); self._render_dashboard()
                if hasattr(self, "identity_dropdown"):
                    self._populate_accounts()
                    self._render_dashboard()
                reporter = getattr(self, "_activity_result", None)
                if callable(reporter):
                    reporter(message)
                else:
                    self.toast(message, 5)
                self._configure_kobo_backup_timer(run_now=False)
                refresh_text()
                settings_state.commit(staged_settings_revisions)
                track_all_settings_widgets()
                update_settings_actions()
                refresh_kobo_backup_status()
                if reconnect_after:
                    self.reconnect_sources()
            return settings_committer.submit(
                "Enregistrement et migration des répertoires…" if directory_change else "Enregistrement de la configuration…",
                staged, ok, control=_button or save,
            )

        def save_and_reconnect(_button=None) -> None:
            save_settings(_button or reconnect, reconnect_after=True)

        (
            copy_text, export_text, persist_diagnostics, clear_diagnostics,
        ) = build_settings_diagnostic_callbacks(
            self, page_lifetime=page_lifetime, refresh_text=refresh_text,
            diag_text=diag_text, level_drop=level_drop,
            diag_switch=diag_switch, levels=levels, Gtk=Gtk, Gdk=Gdk,
        )


        save.connect("clicked", save_settings)
        reconnect.connect("clicked", save_and_reconnect)
        save_bottom.connect("clicked", save_settings)
        reconnect_bottom.connect("clicked", save_and_reconnect)
        diag_filter.connect("notify::selected", lambda *_args: refresh_text())
        refresh.connect("clicked", lambda _b: refresh_text())
        copy.connect("clicked", copy_text)
        export.connect("clicked", export_text)
        level_drop.connect("notify::selected", persist_diagnostics)
        diag_switch.connect("notify::active", persist_diagnostics)

        clear.connect("clicked", clear_diagnostics)
        refresh_text()
        if not standalone:
            self._diag_refresh = lambda: refresh_text() if page_lifetime.alive else None
        return scroll

    def refresh_diagnostics_text(self) -> None:
        cb = getattr(self, "_diag_refresh", None)
        if cb:
            cb()
