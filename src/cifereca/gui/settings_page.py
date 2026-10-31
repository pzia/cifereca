"""Compose extracted Settings sections and controllers; GTK is injected by the window."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from .settings_model import SettingsRevisionState
from .settings_lifetime import SettingsPageLifetime
from .settings_tracking import bind_settings_actions
from .settings_shell import build_settings_shell
from .settings_editor_actions import build_settings_editors
from .settings_library_downloads import build_library_download_section
from .settings_application_interface import build_settings_application_interface
from .settings_legacy_kobo_section import build_kobo_sync_section
from .settings_sync_actions import bind_settings_sync_actions
from .settings_action_bars import build_settings_action_bars
from .settings_ai_section import build_ai_settings_section
from .settings_ai_actions import bind_settings_ai_callbacks
from .settings_diagnostics_section import build_settings_diagnostics_section
from .settings_diagnostics_actions import bind_settings_diagnostics
from .settings_commit_actions import bind_settings_commit


@dataclass(frozen=True)
class SettingsPage:
    scroll: Any
    lifetime: SettingsPageLifetime
    save: Callable[..., bool]
    save_and_reconnect: Callable[..., None]
    refresh: Callable[[], None]

    def dispose(self) -> None:
        self.lifetime.dispose()


def build_settings_page(self, standalone=False, *, Gtk, Adw, Gdk, TaskPriority,
                        AdaptiveActionBar, action_button, add_css, clear_box, mirror_sensitivity):
    page_lifetime = SettingsPageLifetime()
    lifetime_name = "_settings_standalone_lifetime" if standalone else "_settings_main_lifetime"
    previous = getattr(self, lifetime_name, None)
    if previous is not None:
        previous.dispose()
    setattr(self, lifetime_name, page_lifetime)
    shell = build_settings_shell(Gtk=Gtk, Adw=Adw, add_css=add_css)
    scroll = shell.scroll
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

    identity_editors, account_editors = build_settings_editors(
        self, box=box, descriptors=descriptors, public_ids=public_ids,
        mark_settings_dirty=mark_settings_dirty,
        library_directory=lambda: kobo_settings.library_dir_row.get_text(),
        Gtk=Gtk, Adw=Adw, add_css=add_css,
    )

    # ADE is bound only at Paris Identity scope. Provider sub-accounts
    # intentionally have no ADE binding UI.

    # Source download policies remain independent of account credentials.
    download_settings = build_library_download_section(
        section=sections["library"], config=self.config,
        descriptors=descriptors, Gtk=Gtk, Adw=Adw, add_css=add_css,
    )
    download_group = download_settings.download_group

    application_ui = build_settings_application_interface(
        diagnostic_section=sections["diagnostic"],
        interface_section=sections["interface"],
        config=self.config, Gtk=Gtk, Adw=Adw,
        effective_diagnostics_level=self.diagnostics.level,
    )
    application_ui.plugins_button.connect(
        "clicked", lambda _b: self.navigate("plugins"),
    )

    kobo_settings = build_kobo_sync_section(
        section=sections["sync"], download_group=download_group,
        config=self.config, Gtk=Gtk, Adw=Adw,
    )

    settings_bars = build_settings_action_bars(
        root=settings_root, heading=settings_heading,
        adaptive_action_bar=AdaptiveActionBar,
        action_button=action_button, mirror_sensitivity=mirror_sensitivity,
        Gtk=Gtk,
    )
    settings_action_widgets.update({
        "save": (settings_bars.save, settings_bars.save_bottom),
        "reconnect": (settings_bars.reconnect, settings_bars.reconnect_bottom),
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
    capture_ai_settings = bind_settings_ai_callbacks(
        self, ai_ui=ai_ui, ai_cfg=ai_cfg, page_lifetime=page_lifetime,
        settings_state=settings_state,
        update_settings_actions=update_settings_actions,
        Adw=Adw, TaskPriority=TaskPriority,
    )

    tabs.connect("notify::visible-child-name", update_settings_actions)
    track_all_settings_widgets()
    refresh_kobo_backup_status = bind_settings_sync_actions(
        self, page_lifetime=page_lifetime,
        settings_state=settings_state, kobo_settings=kobo_settings,
    )
    update_settings_actions()

    diagnostic_ui = build_settings_diagnostics_section(
        section=sections["diagnostic"], Gtk=Gtk, Adw=Adw,
        adaptive_action_bar=AdaptiveActionBar,
        action_button=self.action_button,
    )
    refresh_text = bind_settings_diagnostics(
        self, page_lifetime=page_lifetime, diagnostic_ui=diagnostic_ui,
        application_ui=application_ui, Gtk=Gtk, Adw=Adw, Gdk=Gdk,
        clear_box=clear_box,
    )
    save_settings, save_and_reconnect = bind_settings_commit(
        self, page_lifetime=page_lifetime, settings_state=settings_state,
        identity_editors=identity_editors, account_editors=account_editors,
        descriptors=descriptors, public_ids=public_ids,
        download_settings=download_settings, application_ui=application_ui,
        kobo_settings=kobo_settings, settings_bars=settings_bars,
        ai_ui=ai_ui, ai_cfg=ai_cfg, capture_ai_settings=capture_ai_settings,
        refresh_text=refresh_text, track_all_settings_widgets=track_all_settings_widgets,
        update_settings_actions=update_settings_actions,
        refresh_kobo_backup_status=refresh_kobo_backup_status,
    )
    for button in (settings_bars.save, settings_bars.save_bottom):
        button.connect("clicked", save_settings)
    for button in (settings_bars.reconnect, settings_bars.reconnect_bottom):
        button.connect("clicked", save_and_reconnect)

    if not standalone:
        self._diag_refresh = lambda: refresh_text() if page_lifetime.alive else None
    return SettingsPage(scroll, page_lifetime, save_settings, save_and_reconnect, refresh_text)
