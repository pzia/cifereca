"""Provider-neutral Settings entry into source-scoped Synchronisation (#3731).

This page constructs only a navigation row and the global library directory.
Provider-owned options are edited from descriptors in the account editor;
retired provider configuration is preserved by the save draft, not displayed
as an editable global consent or silently re-saved on unrelated edits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SyncSettingsControls:
    library_dir_row: Any
    open_sync_button: Any
    sync_navigation_status: Any

    @property
    def run_backup_now(self) -> Any:
        """Old UI port, read-only alias; never invokes a backup."""
        return self.open_sync_button

    @property
    def backup_status(self) -> Any:
        """Old UI port, read-only alias; contains navigation text only."""
        return self.sync_navigation_status


def build_sync_settings_section(
    *, section: Any, download_group: Any, config: Any, Gtk: Any, Adw: Any,
) -> SyncSettingsControls:
    group = Adw.PreferencesGroup()
    group.set_title("Synchronisation par source")
    section.append(group)

    row = Adw.ActionRow()
    row.set_title("Synchronisation — sources et autorisations")
    row.set_subtitle(
        "Gérez les sources, la cadence et l'acquisition autorisée par compte "
        "dans Synchronisation. Aucun consentement n'est déduit des anciens réglages."
    )
    navigate = Gtk.Button(label="Ouvrir Synchronisation…")
    navigate.set_sensitive(True)
    navigate.set_valign(Gtk.Align.CENTER)
    row.add_suffix(navigate)
    group.add(row)

    library = Adw.EntryRow()
    library.set_title("Bibliothèque locale possédée par cifereca")
    library.set_text(str(config.get("library.directory", "~/EBooks") or "~/EBooks"))
    download_group.add(library)
    return SyncSettingsControls(
        library_dir_row=library, open_sync_button=navigate, sync_navigation_status=row,
    )
