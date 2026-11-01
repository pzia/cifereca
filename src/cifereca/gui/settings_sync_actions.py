"""Provider-neutral Settings → Synchronisation navigation; never acquire files."""
from __future__ import annotations


def bind_settings_sync_actions(self, *, page_lifetime, settings_state, sync_settings):
    # Consume only neutral view ports. Legacy attribute aliases are isolated
    # in the compatibility-facing controls, never used by the active binder.
    sync_button = sync_settings.open_sync_button
    sync_status = sync_settings.sync_navigation_status
    def refresh_sync_navigation(*_args) -> None:
        if not page_lifetime.alive or getattr(self, "_closed", False):
            return
        # Source consent is managed by the Sync broker, never by retired
        # global flags. This row is navigation only.
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
        # Navigation is a CoreUI route, not an acquisition authorization.
        # No provider-specific flag may grant or deny access to Sync.
        ready = not settings_state.dirty("sync")
        if ready and page_lifetime.alive and not getattr(self, "_closed", False):
            self.navigate("sync")

    sync_button.connect("clicked", open_sync_page)
    refresh_sync_navigation()
    return refresh_sync_navigation
