"""Provider-neutral Settings → Synchronisation navigation; never acquire files."""
from __future__ import annotations

from .settings_sync_section import kobo_backup_action_state


def bind_settings_sync_actions(self, *, page_lifetime, settings_state, kobo_settings):
    backup_switch = kobo_settings.backup_switch
    backup_dir_row = kobo_settings.backup_dir_row
    backup_interval_row = kobo_settings.backup_interval_row
    vault_profile_drop = kobo_settings.vault_profile_drop
    kobo_run_now = kobo_settings.run_backup_now
    kobo_status = kobo_settings.backup_status
    def refresh_kobo_backup_status(*_args) -> None:
        if not page_lifetime.alive or getattr(self, "_closed", False):
            return
        status, _legacy_ready = kobo_backup_action_state(
            persisted_enabled=(self.config.get("kobo_backup.enabled", False) is True),
            draft_enabled=bool(backup_switch.get_active()),
            dirty=settings_state.dirty("sync"),
        )
        kobo_status.set_subtitle(status)
        # The obsolete Kobo flag is display-only, never a prerequisite for
        # opening provider-neutral Synchronisation. Unsaved edits still block.
        kobo_run_now.set_sensitive(not settings_state.dirty("sync"))

    def open_kobo_sync_page(_button) -> None:
        if not page_lifetime.alive or getattr(self, "_closed", False):
            return
        # Never launch a global download batch from Settings. An identity-
        # scoped inventory refresh and per-source read-only local previews
        # live on Synchronisation; local materialization requires a separate
        # reviewed and explicitly authorized workflow (#1347).
        # Navigation is a CoreUI route, not a Kobo download authorization.
        # No provider-specific flag may grant or deny access to Sync.
        ready = not settings_state.dirty("sync")
        if ready and page_lifetime.alive and not getattr(self, "_closed", False):
            self.navigate("sync")

    kobo_run_now.connect("clicked", open_kobo_sync_page)
    # Subscribe AFTER dirty tracking so the message never claims that an
    # unsaved switch is already active. No provider access from callbacks.
    for widget, signal in (
        (backup_switch, "notify::active"),
        (backup_dir_row, "notify::text"),
        (backup_interval_row, "notify::text"),
        (vault_profile_drop, "notify::selected"),
    ):
        widget.connect(signal, refresh_kobo_backup_status)
    refresh_kobo_backup_status()
    return refresh_kobo_backup_status
