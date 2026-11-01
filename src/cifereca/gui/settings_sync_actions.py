"""Provider-neutral Settings → Synchronisation navigation; never acquire files."""
from __future__ import annotations


def bind_settings_sync_actions(self, *, page_lifetime, settings_state, sync_settings):
    # The neutral Settings page exposes legacy-shaped ports only for the
    # existing widget binder; their actions never invoke provider downloads.
    sync_button = sync_settings.run_backup_now
    sync_status = sync_settings.backup_status
    def refresh_sync_navigation(*_args) -> None:
        if not page_lifetime.alive or getattr(self, "_closed", False):
            return
        # Source consent is managed by the Sync broker, never by the
        # obsolete global kobo_backup.enabled bit. This Settings row is
        # navigation only and must not present the old flag as active.
        dirty = settings_state.dirty("sync")
        sync_status.set_subtitle(
            "Modifications à enregistrer avant d'ouvrir Synchronisation."
            if dirty else
            "Activation, révocation et suivi des synchronisations par source."
        )
        sync_button.set_sensitive(not settings_state.dirty("sync"))

    def open_sync_page(_button) -> None:
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

    sync_button.connect("clicked", open_sync_page)
    refresh_sync_navigation()
    return refresh_sync_navigation
