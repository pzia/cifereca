"""Bind diagnostics controls, refresh and export using the existing diagnostics owners."""
from __future__ import annotations

from .. import __version__
from .diagnostic_presentation import diagnostic_plugin_rows
from .settings_diagnostic_actions import build_settings_diagnostic_callbacks
from .settings_performance_actions import bind_settings_performance_actions
from . import settings_diagnostics_refresh_controller


def bind_diagnostics_page_link(window, button, *, page_lifetime, fallback_widgets=()):
    """Navigate only to a registered Diagnostics page; preserve Core fallback.

    No plugin is activated by building Settings. A disabled/absent contribution
    leaves the existing in-Settings diagnostic tools reachable.
    """

    def active() -> bool:
        return page_lifetime.alive and not getattr(window, "_closed", False)

    def available() -> bool:
        pages = getattr(window, "_pages", None)
        return isinstance(pages, dict) and "diagnostics" in pages

    def refresh() -> bool:
        if not active():
            return False
        enabled = available()
        for widget in fallback_widgets:
            widget.set_visible(not enabled)
        button.set_sensitive(enabled)
        button.set_tooltip_text(
            "Ouvrir la page Diagnostics : journaux et rapports"
            if enabled else
            "Page Diagnostics indisponible ; les outils locaux restent accessibles ici"
        )
        return enabled

    def open_page(_button=None) -> None:
        if not active():
            return
        if not available():
            refresh()
            return
        window.navigate("diagnostics")

    button.connect("clicked", open_page)
    refresh()
    return refresh


def bind_settings_diagnostics(self, *, page_lifetime, diagnostic_ui,
                              application_ui, Gtk, Adw, Gdk, clear_box):
    level_drop = application_ui.level_dropdown
    diag_switch = application_ui.diagnostics_switch
    levels = application_ui.levels
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

    # Route registration is dynamic (plugin enable/disable/rebuild). Do not
    # navigate to an absent page; Settings remains the built-in fallback.
    refresh_diagnostics_link = bind_diagnostics_page_link(
        self, diagnostic_ui.open_page, page_lifetime=page_lifetime,
        fallback_widgets=getattr(diagnostic_ui, "fallback_widgets", ()),
    )

    # Reuse MainWindow.ui_perf: no profiler, periodic collector or automatic
    # detailed capture is started by building the Settings page.
    refresh_performance_status = bind_settings_performance_actions(
        self, page_lifetime=page_lifetime,
        capture_button=diagnostic_ui.performance_capture,
        export_button=diagnostic_ui.performance_export,
        status_label=diagnostic_ui.performance_status,
        Gtk=Gtk,
    )

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
        if not page_lifetime.alive or getattr(self, "_closed", False):
            return
        if refresh_diagnostics_link():
            return
        refresh_performance_status()
        from ..system_status import system_status
        return settings_diagnostics_refresh_controller.refresh_settings_diagnostics(
            self, page_lifetime=page_lifetime,
            diag_summary=diag_summary, diag_filter=diag_filter,
            diag_issues_box=diag_issues_box, diag_text=diag_text,
            open_diagnostic_plugin=open_diagnostic_plugin,
            Gtk=Gtk, Adw=Adw, clear_box=clear_box,
            diagnostic_plugin_rows=diagnostic_plugin_rows,
            system_status=system_status, version=__version__,
        )
    (
        copy_text, export_text, persist_diagnostics, clear_diagnostics,
    ) = build_settings_diagnostic_callbacks(
        self, page_lifetime=page_lifetime, refresh_text=refresh_text,
        diag_text=diag_text, level_drop=level_drop,
        diag_switch=diag_switch, levels=levels, Gtk=Gtk, Gdk=Gdk,
    )

    entry = getattr(diagnostic_ui, "entry", None)
    if entry is not None:
        entry.connect("map", lambda *_args: refresh_diagnostics_link())
    diag_filter.connect("notify::selected", lambda *_args: refresh_text())
    refresh.connect("clicked", lambda _b: refresh_text())
    copy.connect("clicked", copy_text)
    export.connect("clicked", export_text)
    level_drop.connect("notify::selected", persist_diagnostics)
    # The collector is always on; severity is the sole user-facing control.
    clear.connect("clicked", clear_diagnostics)
    refresh_text()
    return refresh_text
