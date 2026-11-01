"""Legacy Kobo Settings compatibility presentation (#3731).

This builder receives Gtk/Adw explicitly. Importing the module remains safe
in Core/CLI headless environments; no settings persistence, network, DRM,
new signals or TaskRunner operations occur here. Widgets remain owned by
MainWindow and participate in its existing dirty/save callbacks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def kobo_backup_action_state(*, persisted_enabled: bool, draft_enabled: bool, dirty: bool) -> tuple[str, bool]:
    """Describe safe source navigation, not permission for local acquisition.

    The existing settings widget is a compatibility alias: its button now
    navigates to the identity-scoped Synchronisation page and never starts a
    global Kobo download batch (#1347).
    """
    if (dirty or type(persisted_enabled) is not bool
            or type(draft_enabled) is not bool or draft_enabled is not persisted_enabled):
        return ("Modifications à enregistrer avant de consulter la synchronisation.", False)
    if persisted_enabled is False:
        return (
            "Ancien réglage global désactivé. Les téléchargements au démarrage "
            "se règlent désormais compte par compte dans Synchronisation ; "
            "l’actualisation des notices ne télécharge aucun livre.", True,
        )
    return (
        "Ancien réglage global enregistré, mais sans effet sur l’acquisition "
        "au démarrage. Seule l’autorisation explicite de chaque compte dans "
        "Synchronisation peut activer ses téléchargements.", True,
    )


@dataclass(frozen=True)
class KoboSyncSettings:
    backup_switch: Any
    backup_status: Any
    run_backup_now: Any
    library_dir_row: Any
    backup_dir_row: Any
    backup_interval_row: Any
    vault_profiles: list[str]
    vault_profile_drop: Any


def build_kobo_sync_section(
    *, section: Any, download_group: Any, config: Any, Gtk: Any, Adw: Any,
) -> KoboSyncSettings:
    """Build original controls in original order, without binding new signals."""
    backup_group = Adw.PreferencesGroup(); backup_group.set_title("Kobo — paramètres techniques existants"); section.append(backup_group)
    # Legacy Kobo global opt-in is obsolete, never an acquisition consent.
    # Keep its detached widget solely for callers of the compatibility API;
    # never parent it into Gtk or present its value as a usable switch.
    backup_switch = Gtk.Switch()
    backup_switch.set_active(config.get("kobo_backup.enabled", False) is True)
    backup_switch.set_sensitive(False)
    manual_row = Adw.ActionRow()
    manual_row.set_title("Synchronisation — sources et autorisations")
    manual_row.set_subtitle(
        "Gérez les sources, la cadence et l'acquisition autorisée par compte "
        "dans Synchronisation. Aucun consentement n'est déduit des anciens réglages."
    )
    backup_status = manual_row
    run_backup_now = Gtk.Button(label="Ouvrir Synchronisation…")
    run_backup_now.set_sensitive(True)
    run_backup_now.set_valign(Gtk.Align.CENTER)
    manual_row.add_suffix(run_backup_now)
    backup_group.add(manual_row)
    library_dir_row = Adw.EntryRow(); library_dir_row.set_title("Bibliothèque locale possédée par cifereca")
    library_dir_row.set_text(str(config.get("library.directory", "~/EBooks") or "~/EBooks")); download_group.add(library_dir_row)
    backup_dir_row = Adw.EntryRow(); backup_dir_row.set_title("Sous-répertoire/override Kobo (vide = bibliothèque locale)")
    backup_dir_row.set_text(str(config.get("kobo_backup.directory", "") or "")); backup_group.add(backup_dir_row)
    backup_interval_row = Adw.EntryRow(); backup_interval_row.set_title("Délai entre tentatives au démarrage (minutes, minimum 15)")
    backup_interval_row.set_text(str(config.get("kobo_backup.interval_minutes", 60) or 60)); backup_group.add(backup_interval_row)
    vault_profiles = ["Android", "Desktop"]
    current_vault_profile = str(config.get("kobo_backup.display_profile", "Android") or "Android")
    if current_vault_profile not in vault_profiles:
        current_vault_profile = "Android"
    vault_display_row = Adw.ActionRow(); vault_display_row.set_title("DisplayProfile Kobo-Vault")
    vault_display_row.set_subtitle("v2.3 : ACSM est prioritaire quand disponible ; le DisplayProfile pilote ensuite SignedNoDrm/KDRM")
    vault_profile_drop = Gtk.DropDown.new_from_strings(vault_profiles)
    vault_profile_drop.set_selected(vault_profiles.index(current_vault_profile))
    vault_profile_drop.set_valign(Gtk.Align.CENTER); vault_display_row.add_suffix(vault_profile_drop); backup_group.add(vault_display_row)
    return KoboSyncSettings(
        backup_switch=backup_switch, backup_status=backup_status,
        run_backup_now=run_backup_now,
        library_dir_row=library_dir_row, backup_dir_row=backup_dir_row,
        backup_interval_row=backup_interval_row, vault_profiles=vault_profiles,
        vault_profile_drop=vault_profile_drop,
    )
