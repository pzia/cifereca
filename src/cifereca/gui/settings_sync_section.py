"""GTK presentation for Kobo/Paris synchronization settings (#1127).

This builder receives Gtk/Adw explicitly. Importing the module remains safe
in Core/CLI headless environments; no settings persistence, network, DRM,
new signals or TaskRunner operations occur here. Widgets remain owned by
MainWindow and participate in its existing dirty/save callbacks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def kobo_backup_action_state(*, persisted_enabled: bool, draft_enabled: bool, dirty: bool) -> tuple[str, bool]:
    """Truthful, side-effect-free status: automation runs on opening, not a timer."""
    if dirty or type(draft_enabled) is not bool or draft_enabled is not persisted_enabled:
        return ("Modifications à enregistrer : aucune acquisition lancée.", False)
    if persisted_enabled is not True:
        return ("Désactivée : activer puis enregistrer pour autoriser une sauvegarde.", False)
    return (
        "Activée : tentative à l’ouverture si intervalle écoulé. "
        "« Lancer maintenant » déclenche une acquisition manuelle explicite.", True,
    )


@dataclass(frozen=True)
class KoboSyncSettings:
    backup_switch: Any
    backup_status: Any
    run_backup_now: Any
    unlinked_switch: Any
    library_dir_row: Any
    backup_dir_row: Any
    backup_interval_row: Any
    vault_profiles: list[str]
    vault_profile_drop: Any


def build_kobo_sync_section(
    *, section: Any, download_group: Any, config: Any, Gtk: Any, Adw: Any,
) -> KoboSyncSettings:
    """Build original controls in original order, without binding new signals."""
    backup_group = Adw.PreferencesGroup(); backup_group.set_title("Acquisition automatique / activité Kobo"); section.append(backup_group)
    backup_auto_row = Adw.ActionRow(); backup_auto_row.set_title("Synchroniser automatiquement les ebooks Kobo")
    backup_auto_row.set_subtitle("Paris résout l’acquisition via Kobo-Vault v2.3+ puis exécute localement via Kobo-Adept contract v8")
    backup_switch = Gtk.Switch(); backup_switch.set_active((config.get("kobo_backup.enabled", False) is True)); backup_switch.set_valign(Gtk.Align.CENTER); backup_auto_row.add_suffix(backup_switch); backup_group.add(backup_auto_row)
    # The source sync page only refreshes online metadata. Downloading books
    # is a separate, explicit operation with the persisted safety settings.
    manual_row = Adw.ActionRow()
    manual_row.set_title("Télécharger/sauvegarder mes livres Kobo")
    status, allowed = kobo_backup_action_state(
        persisted_enabled=(config.get("kobo_backup.enabled", False) is True),
        draft_enabled=(config.get("kobo_backup.enabled", False) is True), dirty=False,
    )
    manual_row.set_subtitle(status)
    backup_status = manual_row
    run_backup_now = Gtk.Button(label="Lancer maintenant")
    run_backup_now.set_sensitive(allowed)
    run_backup_now.set_valign(Gtk.Align.CENTER)
    manual_row.add_suffix(run_backup_now)
    backup_group.add(manual_row)
    unlinked_row = Adw.ActionRow(); unlinked_row.set_title("Autoriser les acquisitions Kobo malgré des livres locaux sans liaison")
    unlinked_row.set_subtitle("Désactivé : suspend les acquisitions du lot en présence d’EPUB importés non rapprochés. Activé : des doublons peuvent être téléchargés.")
    unlinked_switch = Gtk.Switch(); unlinked_switch.set_active(bool(config.get("kobo_backup.allow_unlinked_acquisition", False))); unlinked_switch.set_valign(Gtk.Align.CENTER); unlinked_row.add_suffix(unlinked_switch); backup_group.add(unlinked_row)
    library_dir_row = Adw.EntryRow(); library_dir_row.set_title("Bibliothèque locale possédée par cifereca")
    library_dir_row.set_text(str(config.get("library.directory", "~/EBooks") or "~/EBooks")); download_group.add(library_dir_row)
    backup_dir_row = Adw.EntryRow(); backup_dir_row.set_title("Sous-répertoire/override Kobo (vide = bibliothèque locale)")
    backup_dir_row.set_text(str(config.get("kobo_backup.directory", "") or "")); backup_group.add(backup_dir_row)
    backup_interval_row = Adw.EntryRow(); backup_interval_row.set_title("Intervalle automatique (minutes, minimum 15)")
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
        run_backup_now=run_backup_now, unlinked_switch=unlinked_switch,
        library_dir_row=library_dir_row, backup_dir_row=backup_dir_row,
        backup_interval_row=backup_interval_row, vault_profiles=vault_profiles,
        vault_profile_drop=vault_profile_drop,
    )
